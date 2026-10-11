"""Actual source server with a deterministic lost configuration acknowledgement.

This is test-only: isolated paths, synthetic settings, no workers/model calls.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True)
parser.add_argument("--delay-connection", type=float, default=3.6)
parser.add_argument("--data-dir", required=True)
parser.add_argument("--ready-file", required=True)
parser.add_argument("--watch-parent-stdin", action="store_true")
args = parser.parse_args()
repo = Path(args.repo)
data = Path(args.data_dir)
data.mkdir(parents=True, exist_ok=True)
os.environ["WORKBENCH_DB_PATH"] = str(data / "workbench.sqlite3")
sys.path.insert(0, str(repo / "analysis-worker"))

import uvicorn
from app.workbench.server import create_app

application = create_app(run_worker=False)

class DelayAcknowledgement:
    async def __call__(self, scope, receive, send):
        slow = scope.get("type") == "http" and scope.get("path") == "/api/v2/connection" and scope.get("method") == "PUT"
        async def delayed_send(message):
            if slow and message["type"] == "http.response.start" and message["status"] == 200:
                # The real handler already mutated its own env. Never echo keys.
                (data / "first-put-applied.json").write_text(json.dumps({
                    "pid": os.getpid(), "model": os.environ["DEEPSEEK_MODEL"],
                    "configured": bool(os.environ["DEEPSEEK_API_KEY"]),
                    "key_sha256": hashlib.sha256(os.environ["DEEPSEEK_API_KEY"].encode()).hexdigest(),
                }), encoding="utf-8")
                await asyncio.sleep(args.delay_connection)
            await send(message)
        await application(scope, receive, delayed_send if slow else send)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(128)
ready = Path(args.ready_file)
temporary = ready.with_suffix(".tmp")
temporary.write_text(json.dumps({"port": listener.getsockname()[1], "pid": os.getpid(), "version": "2.0.0"}), encoding="utf-8")
temporary.replace(ready)
server = uvicorn.Server(uvicorn.Config(DelayAcknowledgement(), host="127.0.0.1", port=0, log_config=None, access_log=False, timeout_graceful_shutdown=5))
application.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
try:
    server.run(sockets=[listener])
finally:
    listener.close()
    ready.unlink(missing_ok=True)
