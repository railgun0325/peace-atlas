"""Portable desktop backend. No global Python/Docker installation is used."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading


def prepare_restore_main(source: str, output: str) -> int:
    """Private CLI protocol: no HTTP service, credentials or ordinary DB open."""
    try:
        from app.workbench.restore import prepare_restore
        summary = prepare_restore(Path(source), Path(output), timeout_seconds=60)
        print(json.dumps({"ok": True, "summary": summary}, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "prepare_failed"), "message": getattr(error, "message", str(error))}}, ensure_ascii=False), flush=True)
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument("--ready-file")
    parser.add_argument("--prepare-restore")
    parser.add_argument("--restore-output")
    parser.add_argument("--watch-parent-stdin", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    # Backend source sits beside this launcher in distribution resources/backend.
    source = Path(__file__).resolve().parent
    if not (source / "app").exists():
        source = source.parent / "analysis-worker"
    sys.path.insert(0, str(source))
    if args.prepare_restore or args.restore_output:
        if not args.prepare_restore or not args.restore_output:
            parser.error("--prepare-restore and --restore-output must be provided together")
        if args.data_dir or args.ready_file or args.watch_parent_stdin:
            parser.error("restore preparation cannot start a data service")
        return prepare_restore_main(args.prepare_restore, args.restore_output)
    if not args.data_dir or not args.ready_file:
        parser.error("--data-dir and --ready-file are required for the data service")
    data = Path(args.data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(data / "logs" / "backend.log", encoding="utf-8")], format="%(asctime)s %(levelname)s %(name)s %(message)s")
    os.environ["WORKBENCH_DATA_DIR"] = str(data)
    os.environ["WORKBENCH_SOCIAL_DIR"] = str(data / "social")
    browsers = source.parent / "browsers"
    if browsers.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        os.environ.pop("PLAYWRIGHT_NODEJS_PATH", None)
    os.environ["WORKBENCH_DB_PATH"] = str(data / "workbench.sqlite3")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    import uvicorn
    from app.workbench.server import create_app
    application = create_app()
    config = uvicorn.Config(application, host="127.0.0.1", port=args.port, log_config=None, access_log=False, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    application.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    if args.watch_parent_stdin:
        def watch_parent():
            try:
                # A daemon blocked in BufferedReader.read can retain its lock
                # during normal interpreter shutdown and abort Python. Read
                # the owned OS pipe directly so HTTP shutdown can exit cleanly
                # while the desktop still holds its end of the pipe open.
                while os.read(sys.stdin.fileno(), 4096):
                    pass
            except (OSError, ValueError):
                pass
            logging.getLogger(__name__).warning("桌面父进程管道已关闭；请求正常退出")
            server.should_exit = True
        threading.Thread(target=watch_parent, name="desktop-parent-watch", daemon=True).start()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    ready = Path(args.ready_file)
    temporary = ready.with_suffix(".tmp")
    temporary.write_text(json.dumps({"port": listener.getsockname()[1], "pid": os.getpid(), "version": "2.0.0"}), encoding="utf-8")
    temporary.replace(ready)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        ready.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
