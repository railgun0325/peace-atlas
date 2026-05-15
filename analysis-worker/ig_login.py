"""
Instagram Playwright 首次登录
=============================
打开浏览器窗口让你手动登录一次 Instagram，
登录态保存到 data/ig_browser_profile/，
后续自动采集无需再次登录。

运行：
  python ig_login.py
"""

import asyncio

async def main():
    print("正在打开浏览器…")
    print("请在弹出的浏览器窗口中登录 Instagram")
    print("登录成功后，关闭浏览器窗口即可")
    print()

    # 强制重新登录
    from pathlib import Path
    state_file = Path(__file__).resolve().parent / "data" / "ig_browser_profile" / "state.json"
    ready_file = Path(__file__).resolve().parent / "data" / "ig_browser_profile" / ".ready"
    state_file.unlink(missing_ok=True)
    ready_file.unlink(missing_ok=True)

    # 导入并初始化
    from app.ig_playwright import _ensure_ready, _cleanup_browser
    browser, err = await _ensure_ready()

    if err:
        print(f"❌ {err}")
        print()
        print("请先安装依赖:")
        print("  pip install playwright")
        print("  playwright install chromium")
        return

    print("✅ 登录成功！浏览器状态已保存。")
    print("以后运行采集时会自动复用，无需再次登录。")
    await _cleanup_browser()

if __name__ == "__main__":
    asyncio.run(main())
