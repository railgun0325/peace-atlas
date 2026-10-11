"""Developer start: python scripts/run_workbench.py (desktop users run the app)."""
from pathlib import Path
import argparse
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis-worker"))


def load_local_ai_environment():
    # Preserve existing selected provider settings without ever copying them into
    # a distribution, logging them, or interpreting environment text as code.
    source = ROOT / ".env"
    if not source.exists():
        return
    allowed = {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_TIMEOUT_SECONDS"}
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in allowed and key not in os.environ:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--data", help="Separate SQLite database path for tests")
    args = parser.parse_args()
    if args.data:
        os.environ["WORKBENCH_DB_PATH"] = str(Path(args.data).resolve())
    load_local_ai_environment()
    import uvicorn
    from app.workbench.server import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
