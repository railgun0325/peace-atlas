from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


STATUS_LOCK = threading.Lock()
STATUS: dict[str, object] = {
    "ok": True,
    "status": "idle",
    "message": "Instagram login helper is ready.",
}
LOGIN_THREAD: threading.Thread | None = None


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _set_status(**payload: object) -> None:
    with STATUS_LOCK:
        STATUS.update(payload)


def _get_status() -> dict[str, object]:
    with STATUS_LOCK:
        return dict(STATUS)


def _run_login(force_reset: bool, timeout_seconds: int) -> None:
    try:
        from app.instagram_safe import prepare_login_session

        _set_status(ok=True, status="login_window_open", message="Login browser is open. Complete Instagram login there.")
        result = asyncio.run(
            prepare_login_session(force_reset=force_reset, timeout_seconds=timeout_seconds)
        )
        _set_status(**result)
    except Exception as exc:
        _set_status(ok=False, status="helper_error", message=str(exc))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_OPTIONS(self) -> None:
        _json(self, 200, {"ok": True})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json(self, 200, {"ok": True, "status": "ready"})
            return
        if parsed.path == "/instagram/login/status":
            _json(self, 200, _get_status())
            return
        if parsed.path == "/instagram/session":
            try:
                from app.instagram_safe import assess_login_readiness

                _json(self, 200, assess_login_readiness())
            except Exception as exc:
                _json(self, 500, {"ok": False, "status": "helper_error", "message": str(exc)})
            return
        _json(self, 404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:
        global LOGIN_THREAD
        parsed = urlparse(self.path)
        if parsed.path == "/instagram/login/start":
            query = parse_qs(parsed.query)
            force_reset = str(query.get("force_reset", ["false"])[0]).lower() in {"1", "true", "yes"}
            timeout_seconds = int(query.get("timeout", ["600"])[0] or 600)
            if LOGIN_THREAD and LOGIN_THREAD.is_alive():
                _json(self, 409, {**_get_status(), "message": "Login window is already open."})
                return
            LOGIN_THREAD = threading.Thread(
                target=_run_login,
                args=(force_reset, timeout_seconds),
                daemon=True,
            )
            LOGIN_THREAD.start()
            _json(self, 200, {"ok": True, "status": "starting", "message": "Opening Instagram login window."})
            return
        if parsed.path == "/instagram/session/clear":
            try:
                from app.instagram_safe import clear_login_session

                result = clear_login_session()
                _set_status(**result)
                _json(self, 200, result)
            except Exception as exc:
                _json(self, 500, {"ok": False, "status": "helper_error", "message": str(exc)})
            return
        _json(self, 404, {"ok": False, "message": "not found"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18190)
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    worker_root = project_root / "analysis-worker"
    sys.path.insert(0, str(worker_root))

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Instagram login helper listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
