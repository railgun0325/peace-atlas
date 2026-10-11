"""Offline browser acceptance: private interpreter/driver, no saved accounts."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


async def check_browser():
    from playwright.async_api import async_playwright
    checks = []
    async with async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path).resolve()
        root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).resolve()
        if not executable.is_file() or not executable.is_relative_to(root):
            raise RuntimeError("Browser executable is not inside the bundled browser directory")
        for visible in (False, True):
            browser = await playwright.chromium.launch(
                executable_path=str(executable), headless=not visible, timeout=30_000)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto("about:blank")
                await page.set_content("<title>PEACE ATLAS offline browser check</title><p>Private browser ready</p>")
                assert await page.title() == "PEACE ATLAS offline browser check"
                assert await page.locator("p").inner_text() == "Private browser ready"
                checks.append({"mode": "visible" if visible else "headless", "version": browser.version,
                               "url": page.url, "render": "passed"})
                await context.close()
            finally:
                await browser.close()
            if browser.is_connected():
                raise RuntimeError("Browser failed to disconnect after close")
    return {"ok": True, "python": sys.executable, "executable": str(executable), "checks": checks,
            "network": "no external navigation", "account_state": "none read or written"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browsers-path", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(args.browsers_path).resolve())
    os.environ.pop("PLAYWRIGHT_NODEJS_PATH", None)
    result = asyncio.run(check_browser())
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
