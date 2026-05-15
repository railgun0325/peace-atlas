from __future__ import annotations

import asyncio
import argparse

from app.instagram_safe import assess_login_readiness, prepare_login_session


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Clear old Instagram login state before opening the login window.")
    parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait for login completion.")
    args = parser.parse_args()

    status = assess_login_readiness()
    print(f"[instagram] readiness: {status['status']} - {status['message']}")
    result = await prepare_login_session(force_reset=args.reset, timeout_seconds=args.timeout)
    print(f"[instagram] login_prepare: {result['status']} - {result['message']}")
    if not result.get("ok"):
        print("Tip: run this on a machine with a visible browser profile and Playwright installed.")


if __name__ == "__main__":
    asyncio.run(main())
