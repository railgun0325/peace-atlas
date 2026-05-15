"""
Instagram Playwright 采集器
===========================
使用 Playwright 模拟真实浏览器行为采集 Instagram 公开帖子。

为什么用 Playwright 而不是 Instagrapi：
- Instagrapi 走私有 API → Instagram 检测为 bot → 封号
- Playwright 走真实浏览器 → 看起来像人在刷 → 封号风险大幅降低

工作方式：
  首次运行: 打开可视化窗口 → 人工登录一次 → 保存浏览器状态
  后续运行: headless 复用已保存的登录态 → 自动采集

进度反馈：
  每条帖子即时 yield，通过 FastAPI SSE 流式推送到前端。
  yield {"type": "ig_post", "shortcode": "...", "caption": "...", ...}
  yield {"type": "ig_progress", "message": "..."}
  yield {"type": "ig_error", "message": "..."}

依赖：
  pip install playwright
  playwright install chromium

浏览器状态存储位置：
  {project}/data/ig_browser_profile/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger("ig_playwright")

# ── 配置 ──────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
USER_DATA_DIR = PROJECT_DIR / "data" / "ig_browser_profile"
STATE_FILE = USER_DATA_DIR / "state.json"
READY_FILE = USER_DATA_DIR / ".ready"  # 标记登录已完成

# 人类行为模拟参数
MIN_DELAY = 2.0       # 操作间最小延迟（秒）
MAX_DELAY = 5.0       # 操作间最大延迟（秒）
SCROLL_WAIT = 3.0     # 滚动后等待加载（秒）
MAX_POSTS_PER_PROFILE = 15   # 每个 profile 最多采集帖子数
MAX_POSTS_PER_HASHTAG = 10   # 每个 hashtag 最多采集帖子数

# 移动端 User-Agent（Instagram 对移动端更宽松）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

# ── 浏览器单例 ────────────────────────────────────────────
_browser_lock = asyncio.Lock()
_browser = None   # (playwright_instance, context)
_ready = False


async def _random_delay(min_s: float = MIN_DELAY, max_s: float = MAX_DELAY):
    """随机延迟，模拟人类浏览节奏。"""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _ensure_ready():
    """确保浏览器已登录 Instagram 且可用。"""
    global _browser, _ready

    if _ready and _browser is not None:
        return _browser, None

    async with _browser_lock:
        if _ready and _browser is not None:
            return _browser, None

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None, "playwright 未安装：pip install playwright && playwright install chromium"

        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        has_state = STATE_FILE.exists() and READY_FILE.exists()

        try:
            pw = await async_playwright().start()
        except Exception as e:
            return None, f"Playwright 启动失败: {e}（尝试 playwright install chromium）"

        context = None
        try:
            context = await pw.chromium.launch_persistent_context(
                str(USER_DATA_DIR),
                headless=has_state,  # 有登录态则 headless，无则可视化
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
                viewport={"width": 390, "height": 844},  # iPhone 14
                user_agent=MOBILE_UA,
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
            )

            # 注入反检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'ja', 'en-US', 'en']});
                window.chrome = {runtime: {}};
            """)

            page = await context.new_page()

            if not has_state:
                # ═══════════════════════════════════════════
                # 首次运行：打开可视化浏览器让用户手动登录
                # ═══════════════════════════════════════════
                await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

                # 如果页面本身已有 cookie 登陆态（可能之前浏览器登过），跳过手动登录
                page_url = page.url.lower()
                page_text = (await page.content())[:1000].lower()
                already_logged = "login" not in page_url and "log in" not in page_text[:200]

                if not already_logged:
                    # 需要手动登录 —— 只有非 Docker 环境下才能做到
                    # Docker headless 环境无法交互，走 Bing RSS 回退
                    if os.environ.get("DOCKER_ENV") or not sys.stdin.isatty():
                        await context.close()
                        await pw.stop()
                        return None, (
                            "Instagram 首次登录需要交互式浏览器窗口。"
                            "请在 Windows 终端运行: python -m app.ig_playwright --login"
                        )

                    logger.info("=" * 50)
                    logger.info("请在打开的浏览器窗口中手动登录 Instagram")
                    logger.info("登录成功后关闭浏览器窗口即可")
                    logger.info("=" * 50)
                    # 等待用户关闭浏览器（检测页面关闭或 context 关闭）
                    try:
                        await page.wait_for_url("**/instagram.com/**", timeout=300)
                    except Exception:
                        pass
                    # 给用户时间
                    await asyncio.sleep(5)

                # 保存状态
                await context.storage_state(path=str(STATE_FILE))
                READY_FILE.touch()
                logger.info("✅ Instagram 登录态已保存到 %s", STATE_FILE)

            # ═══════════════════════════════════════════
            # 验证登录态是否有效
            # ═══════════════════════════════════════════
            try:
                check_page = await context.new_page()
                await check_page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(1.5)
                content = await check_page.content()
                content_lower = content[:2000].lower()
                await check_page.close()

                if "login" in check_page.url.lower() or "log in" in content_lower[:300]:
                    # 登录态过期
                    STATE_FILE.unlink(missing_ok=True)
                    READY_FILE.unlink(missing_ok=True)
                    await context.close()
                    await pw.stop()
                    return None, "Instagram 登录态已过期，请删除 data/ig_browser_profile 后重新运行"

                logger.info("✅ Instagram 登录态有效")
            except Exception as check_err:
                logger.warning("登录态检查失败: %s（尝试继续）", check_err)

            _browser = (pw, context)
            _ready = True
            return _browser, None

        except Exception as e:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            try:
                await pw.stop()
            except Exception:
                pass
            return None, f"浏览器初始化失败: {e}"


async def _cleanup_browser():
    """关闭浏览器（模块卸载时调用）。"""
    global _browser, _ready
    if _browser:
        pw, context = _browser
        try:
            await context.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass
        _browser = None
        _ready = False


# ═══════════════════════════════════════════════════════════
# 核心采集函数
# ═══════════════════════════════════════════════════════════

async def collect_profile_streaming(
    handle: str,
    max_posts: int = MAX_POSTS_PER_PROFILE,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式采集 Instagram 账号的最新帖子。

    每条帖子即时 yield：
      {"type": "ig_post", "shortcode": "...", "caption": "...", "url": "...", ...}
    """
    (pw, context), err = await _ensure_ready()
    if err:
        yield {"type": "ig_error", "platform": "instagram", "handle": handle, "message": err}
        return

    username = handle.strip().lstrip("@")
    profile_url = f"https://www.instagram.com/{username}/"

    page = await context.new_page()
    post_count = 0

    try:
        # 注入反检测 + 滚动脚本
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "handle": handle,
            "message": f"正在打开 {username} 的主页…",
        }

        # 导航到 profile
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=25000)
        await _random_delay(2, 4)

        # 检查是否被拦截或要求登录
        page_text = await page.content()
        if "login" in page.url.lower() or "Log In" in page_text[:500]:
            yield {
                "type": "ig_error",
                "platform": "instagram",
                "handle": handle,
                "message": "登录态失效，需要重新登录",
            }
            return

        if "Sorry, this page isn't available" in page_text[:1000]:
            yield {
                "type": "ig_error",
                "platform": "instagram",
                "handle": handle,
                "message": f"账号 {username} 不存在或已设为私密",
            }
            return

        # 提取帖子链接（Instagram 在页面中以 <a href="/p/SHORTCODE/"> 形式存在）
        seen_shortcodes: set[str] = set()
        scroll_attempts = 0
        max_scrolls = max(5, max_posts // 2)  # 每次滚动约加载 2-3 个帖子

        while len(seen_shortcodes) < max_posts and scroll_attempts < max_scrolls:
            # 提取当前页面的帖子链接
            links = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                const results = [];
                links.forEach(link => {
                    const href = link.getAttribute('href');
                    if (href) {
                        const match = href.match(/\\/(p|reel)\\/([^/?&#]+)/);
                        if (match) {
                            results.push({
                                shortcode: match[2],
                                url: 'https://www.instagram.com' + href.split('?')[0]
                            });
                        }
                    }
                });
                return results;
            }""")

            for link_info in links:
                sc = link_info["shortcode"]
                if sc not in seen_shortcodes:
                    seen_shortcodes.add(sc)

                    # 尝试获取更多信息
                    try:
                        post_data = await _extract_post_detail(page, sc)
                        yield {
                            "type": "ig_post",
                            "shortcode": sc,
                            "url": link_info["url"],
                            "handle": handle,
                            "caption": post_data.get("caption", "")[:300],
                            "likes": post_data.get("likes", 0),
                            "comments": post_data.get("comments", 0),
                            "taken_at": post_data.get("taken_at"),
                            "is_video": post_data.get("is_video", False),
                        }
                        post_count += 1
                    except Exception:
                        # 最少信息也够
                        yield {
                            "type": "ig_post",
                            "shortcode": sc,
                            "url": link_info["url"],
                            "handle": handle,
                            "caption": "",
                            "likes": 0,
                            "comments": 0,
                            "taken_at": None,
                            "is_video": False,
                        }
                        post_count += 1

                    if post_count >= max_posts:
                        break

            if post_count >= max_posts:
                break

            # 模拟人类滚动
            await page.evaluate("""() => {
                window.scrollBy(0, window.innerHeight * 0.8);
            }""")
            scroll_attempts += 1
            yield {
                "type": "ig_progress",
                "platform": "instagram",
                "handle": handle,
                "message": f"{username}: 已找到 {post_count} 条帖子（滚动 {scroll_attempts}/{max_scrolls}）…",
            }
            await asyncio.sleep(SCROLL_WAIT)
            await _random_delay(1, 2)

        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "handle": handle,
            "message": f"{username}: 完成，共 {post_count} 条帖子",
        }

    except Exception as e:
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "handle": handle,
            "message": f"采集失败: {e}",
        }
    finally:
        await page.close()


async def collect_hashtag_streaming(
    hashtag: str,
    max_posts: int = MAX_POSTS_PER_HASHTAG,
) -> AsyncGenerator[dict[str, Any], None]:
    """流式采集 Instagram 标签的最新帖子。"""
    (pw, context), err = await _ensure_ready()
    if err:
        yield {"type": "ig_error", "platform": "instagram", "hashtag": hashtag, "message": err}
        return

    tag = hashtag.strip().lstrip("#")
    tag_url = f"https://www.instagram.com/explore/tags/{tag}/"

    page = await context.new_page()
    post_count = 0

    try:
        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "hashtag": tag,
            "message": f"正在打开 #{tag}…",
        }

        await page.goto(tag_url, wait_until="domcontentloaded", timeout=25000)
        await _random_delay(2, 4)

        page_text = await page.content()
        if "login" in page.url.lower():
            yield {
                "type": "ig_error",
                "platform": "instagram",
                "hashtag": tag,
                "message": "登录态失效",
            }
            return

        seen_shortcodes: set[str] = set()
        scroll_attempts = 0
        max_scrolls = max(3, max_posts // 2)

        while len(seen_shortcodes) < max_posts and scroll_attempts < max_scrolls:
            links = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                const results = [];
                links.forEach(link => {
                    const href = link.getAttribute('href');
                    if (href) {
                        const match = href.match(/\\/(p|reel)\\/([^/?&#]+)/);
                        if (match) results.push(match[2]);
                    }
                });
                return [...new Set(results)];
            }""")

            for sc in links:
                if sc not in seen_shortcodes:
                    seen_shortcodes.add(sc)
                    yield {
                        "type": "ig_post",
                        "shortcode": sc,
                        "url": f"https://www.instagram.com/p/{sc}/",
                        "hashtag": tag,
                        "caption": f"#{tag}",
                        "likes": 0,
                        "comments": 0,
                        "taken_at": None,
                        "is_video": False,
                    }
                    post_count += 1
                    if post_count >= max_posts:
                        break

            if post_count >= max_posts:
                break

            await page.evaluate("""() => { window.scrollBy(0, window.innerHeight * 0.7); }""")
            scroll_attempts += 1
            yield {
                "type": "ig_progress",
                "platform": "instagram",
                "hashtag": tag,
                "message": f"#{tag}: 已找到 {post_count} 条",
            }
            await asyncio.sleep(SCROLL_WAIT)
            await _random_delay(1, 2)

        yield {
            "type": "ig_progress",
            "platform": "instagram",
            "hashtag": tag,
            "message": f"#{tag}: 完成，共 {post_count} 条",
        }

    except Exception as e:
        yield {
            "type": "ig_error",
            "platform": "instagram",
            "hashtag": tag,
            "message": f"标签采集失败: {e}",
        }
    finally:
        await page.close()


async def _extract_post_detail(page, shortcode: str) -> dict[str, Any]:
    """尝试从当前页面提取帖子的更多信息（标题、点赞等）。

    这个方法通过 page.evaluate 查找页面上对应帖子的 DOM 元素。
    不同 Instagram 页面结构不同，提取是 best-effort。
    """
    try:
        data = await page.evaluate("""(shortcode) => {
            const links = document.querySelectorAll('a[href*="/p/' + shortcode + '"], a[href*="/reel/' + shortcode + '"]');
            let result = {caption: '', likes: 0, comments: 0, taken_at: null, is_video: false};

            for (const link of links) {
                // 向上查找帖子容器
                let container = link.closest('article') || link.closest('[role="article"]') || link.parentElement?.parentElement;
                if (!container) continue;

                // 查找标题
                const captions = container.querySelectorAll('h1, [dir="auto"], span');
                for (const el of captions) {
                    const text = el.textContent?.trim();
                    if (text && text.length > 5 && !text.startsWith('#') && !text.includes('View all')) {
                        result.caption = text.slice(0, 300);
                        break;
                    }
                }

                // 查找互动数
                const allSpans = container.querySelectorAll('span');
                for (const span of allSpans) {
                    const text = span.textContent?.trim() || '';
                    const numMatch = text.match(/^([0-9,.]+[KMB]?)\\s*(likes?|件)?$/i);
                    if (numMatch) {
                        result.likes = parseCount(numMatch[1]);
                        break;
                    }
                }

                // 检测视频
                const videos = container.querySelectorAll('video');
                if (videos.length > 0) result.is_video = true;

                if (result.caption) break;
            }
            return result;
        }""", shortcode)
        return data
    except Exception:
        return {"caption": "", "likes": 0, "comments": 0, "taken_at": None, "is_video": False}


# ═══════════════════════════════════════════════════════════
# 独立运行入口（测试/首次登录用）
# ═══════════════════════════════════════════════════════════

async def _main():
    """独立运行：测试 Playwright 采集器。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="Instagram Playwright 采集器")
    parser.add_argument("--login", action="store_true", help="仅登录并保存状态")
    parser.add_argument("--profile", type=str, help="采集指定账号")
    parser.add_argument("--hashtag", type=str, help="采集指定标签")
    parser.add_argument("--max", type=int, default=10, help="最大帖子数")
    args = parser.parse_args()

    if args.login:
        # 删除旧状态，强制重新登录
        STATE_FILE.unlink(missing_ok=True)
        READY_FILE.unlink(missing_ok=True)
        browser, err = await _ensure_ready()
        if err:
            print(f"❌ {err}")
        else:
            print("✅ 登录成功，状态已保存")
        await _cleanup_browser()
        return

    if args.profile:
        print(f"开始采集 @{args.profile}…\n")
        async for event in collect_profile_streaming(args.profile, args.max):
            if event["type"] == "ig_post":
                print(f"  📷 {event['shortcode']}: {event.get('caption', '')[:80]}")
            elif event["type"] == "ig_progress":
                print(f"  ⏳ {event['message']}")
            elif event["type"] == "ig_error":
                print(f"  ❌ {event['message']}")
        await _cleanup_browser()
        return

    if args.hashtag:
        print(f"开始采集 #{args.hashtag}…\n")
        async for event in collect_hashtag_streaming(args.hashtag, args.max):
            if event["type"] == "ig_post":
                print(f"  📷 {event['shortcode']}")
            elif event["type"] == "ig_progress":
                print(f"  ⏳ {event['message']}")
            elif event["type"] == "ig_error":
                print(f"  ❌ {event['message']}")
        await _cleanup_browser()
        return

    parser.print_help()


if __name__ == "__main__":
    asyncio.run(_main())
