"""
独立社媒采集器 — Twitter (Nitter RSS) + Instagram (Instagrapi)
==============================================================
可在 Docker 外直接运行，10分钟自动循环。
不依赖 PostgreSQL / FastAPI / Docker。

使用方法:
  python standalone_social_collector.py           # 运行一次
  python standalone_social_collector.py --loop    # 无限循环 (10分钟间隔)

依赖:
  pip install feedparser httpx instagrapi beautifulsoup4

环境变量 (.env 或直接设置):
  INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD   (Instagram 登录)
  PROXY_URL                                 (代理，如 http://127.0.0.1:7897)
  OUTBOUND_HTTP_PROXY                       (Docker桥接代理，备用)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re as _re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── 项目路径 ──────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(DATA_DIR / "standalone_collector.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("standalone")

# ── 代理 (优先级递减) ─────────────────────────────────────
def _get_proxy() -> str:
    return (
        os.environ.get("PROXY_URL", "")
        or os.environ.get("OUTBOUND_HTTP_PROXY", "")
        or os.environ.get("HTTP_PROXY", "")
        or os.environ.get("HTTPS_PROXY", "")
        or ""
    )

PROXY_URL = _get_proxy()

# ── Nitter 实例 ──────────────────────────────────────────
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.cz",
]
NITTER_FALLBACK = [
    "https://nitter.poast.org",
    "https://nitter.privacyredirect.com",
]

# ── 监控账号 ──────────────────────────────────────────────
TWITTER_ACCOUNTS = {
    "琉球新報": "ryukyushimpo",
    "沖縄タイムス": "theokinawatimes",
    "朝日新聞": "asahi",
    "毎日新聞": "mainichi",
    "ピースボート": "peace_boat",
    "岩波書店": "Iwanamishoten",
    "全労連": "zenroren",
    "グリーンピース日本": "GreenpeaceJP",
    "望月衣塑子": "ISOKO_MOCHIZUKI",
}

INSTAGRAM_ACCOUNTS = {
    "ピースボート": "peace_boat",
    "沖縄タイムス": "okinawatimes",
    "朝日新聞": "asahi_shimbun",
    "岩波書店": "iwanamishoten",
}

INSTAGRAM_HASHTAGS = [
    "反戦", "平和運動", "護憲", "脱原発",
    "核廃絶", "FreePalestine", "AntiWar", "反基地",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ja-JP,en-US;q=0.9,ja;q=0.8",
}


# ═══════════════════════════════════════════════════════════
# Twitter: Nitter RSS (无需登录)
# ═══════════════════════════════════════════════════════════

async def fetch_nitter_rss(username: str) -> list[dict]:
    """从 Nitter RSS 获取用户推文。"""
    import httpx
    import xml.etree.ElementTree as ET

    all_nitter = NITTER_INSTANCES + NITTER_FALLBACK
    for nitter in all_nitter:
        try:
            rss_url = f"{nitter}/{username}/rss"
            proxy = _get_proxy()
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True,
                proxy=proxy if proxy else None,
            ) as client:
                resp = await client.get(rss_url, headers=HEADERS)
                if resp.status_code != 200:
                    continue

                # 去除 XML 命名空间
                text = resp.text
                text = _re.sub(r'\s+xmlns[^=]*="[^"]*"', "", text)
                text = _re.sub(r'\s+xmlns:[^=]*="[^"]*"', "", text)
                text = _re.sub(r'<(/)?[\w-]+:', r'<\1', text)

                root = ET.fromstring(text)
                items = []
                for entry in root.findall(".//item"):
                    title_el = entry.find("title")
                    link_el = entry.find("link")
                    desc_el = entry.find("description")
                    pubdate_el = entry.find("pubDate")

                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    content = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                    content = _re.sub(r"<[^>]+>", "", content)
                    content = _re.sub(r"&amp;", "&", content)
                    content = _re.sub(r"&lt;", "<", content)
                    content = _re.sub(r"&gt;", ">", content)

                    if not title and not content:
                        continue

                    items.append({
                        "source_type": "twitter",
                        "source_name": f"@{username}",
                        "title": title[:200],
                        "text": content[:2000],
                        "url": link,
                        "author": username,
                        "published_at": None,  # RSS pubDate 解析略
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "method": f"nitter:{nitter.split('//')[1]}",
                    })
                if items:
                    logger.info(f"  @{username}: {len(items)} tweets (via {nitter})")
                    return items
        except Exception as e:
            continue
    logger.warning(f"  @{username}: 所有 Nitter 实例不可用")
    return []


# ═══════════════════════════════════════════════════════════
# Instagram: Instagrapi (需要登录)
# ═══════════════════════════════════════════════════════════

_ig_client = None

def _get_ig_client():
    """获取或创建 Instagrapi 客户端（同步，单例）。"""
    global _ig_client
    if _ig_client is not None:
        return _ig_client

    try:
        from instagrapi import Client
    except ImportError:
        logger.error("instagrapi 未安装: pip install instagrapi")
        return None

    username = os.environ.get("INSTAGRAM_USERNAME", "")
    password = os.environ.get("INSTAGRAM_PASSWORD", "")
    if not username or not password:
        logger.error("INSTAGRAM_USERNAME/INSTAGRAM_PASSWORD 未设置")
        return None

    cl = Client()
    proxy = _get_proxy()
    if proxy:
        cl.set_proxy(proxy)
        logger.info(f"IG proxy: {proxy}")
    cl.delay_range = [3, 8]

    session_file = DATA_DIR / "instagram_session.json"
    try:
        cl.load_settings(str(session_file))
        cl.login(username, password)
        logger.info("IG 登录成功 (session)")
        _ig_client = cl
        return cl
    except Exception:
        pass

    try:
        cl.login(username, password)
        cl.dump_settings(str(session_file))
        logger.info(f"IG 登录成功: @{username}")
        _ig_client = cl
        return cl
    except Exception as e:
        logger.error(f"IG 登录失败: {e}")
        return None


def collect_ig_account(cl, username: str, display_name: str) -> list[dict]:
    """采集 Instagram 账号的最新帖子。"""
    items = []
    try:
        user_id = cl.user_id_from_username(username)
        user = cl.user_info(user_id)
        medias = cl.user_medias(user_id, amount=10)
        logger.info(f"  [{display_name} @{username}]: {len(medias)} posts "
                    f"(followers: {user.follower_count})")
        for m in medias:
            items.append({
                "source_type": "instagram",
                "source_name": f"@{username}",
                "post_id": m.pk,
                "shortcode": m.code,
                "caption": (m.caption_text or "")[:500],
                "likes": m.like_count,
                "comments": m.comment_count,
                "taken_at": str(m.taken_at),
                "url": f"https://www.instagram.com/p/{m.code}/",
                "media_type": getattr(m, 'media_type', 1),
                "display_name": display_name,
                "followers": user.follower_count,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        logger.warning(f"  [{display_name} @{username}]: {e}")
    return items


def collect_ig_hashtags(cl, hashtag: str, amount: int = 3) -> list[dict]:
    """采集 Instagram 标签的最新帖子。"""
    items = []
    try:
        tag = hashtag.strip().lstrip("#")
        medias = cl.hashtag_medias_recent(tag, amount=amount)
        logger.info(f"  #{tag}: {len(medias)} posts")
        for m in medias:
            items.append({
                "source_type": "instagram_hashtag",
                "source_name": f"#{hashtag}",
                "post_id": m.pk,
                "shortcode": m.code,
                "caption": (m.caption_text or "")[:500],
                "likes": m.like_count,
                "comments": m.comment_count,
                "taken_at": str(m.taken_at),
                "url": f"https://www.instagram.com/p/{m.code}/",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        logger.warning(f"  #{hashtag}: {e}")
    return items


# ═══════════════════════════════════════════════════════════
# 保存 (去重)
# ═══════════════════════════════════════════════════════════

def save_results(items: list[dict], filename: str):
    filepath = DATA_DIR / filename
    existing = []
    existing_keys = set()
    if filepath.exists():
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for item in existing:
                key = item.get("url", "") or str(item.get("post_id", ""))
                if key:
                    existing_keys.add(key)
        except Exception:
            pass

    new_count = 0
    for item in items:
        key = item.get("url", "") or str(item.get("post_id", ""))
        if key and key not in existing_keys:
            existing.append(item)
            existing_keys.add(key)
            new_count += 1

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info(f"  Save {filename}: +{new_count} new / {len(existing)} total")


# ═══════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════

async def run_once():
    logger.info("=" * 60)
    logger.info(f"采集开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    twitter_items = []
    ig_items = []

    # ── Twitter (Nitter RSS) ──
    logger.info("── Twitter/X ──")
    for display_name, username in TWITTER_ACCOUNTS.items():
        tweets = await fetch_nitter_rss(username)
        twitter_items.extend(tweets)

    if twitter_items:
        save_results(twitter_items, "twitter_standalone_results.json")

    # ── Instagram (Instagrapi) ──
    logger.info("── Instagram ──")
    cl = _get_ig_client()
    if cl:
        for display_name, username in INSTAGRAM_ACCOUNTS.items():
            posts = collect_ig_account(cl, username, display_name)
            ig_items.extend(posts)
            time.sleep(5)  # 账号间延迟

        for hashtag in INSTAGRAM_HASHTAGS:
            posts = collect_ig_hashtags(cl, hashtag, amount=2)
            ig_items.extend(posts)
            time.sleep(8)  # 标签间延迟

        if ig_items:
            save_results(ig_items, "instagram_standalone_results.json")
    else:
        logger.warning("Instagram 客户端不可用，跳过 IG 采集")

    total = len(twitter_items) + len(ig_items)
    logger.info(f"完成: Twitter {len(twitter_items)} + Instagram {len(ig_items)} = {total} 条")
    return total


async def run_loop(interval: int = 600):
    logger.info(f"循环模式: 每 {interval}s ({interval/60:.0f}分钟)")
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"\n{'─'*40}\nCycle #{cycle}\n{'─'*40}")
        try:
            await run_once()
        except KeyboardInterrupt:
            logger.info("用户中断")
            break
        except Exception as e:
            logger.error(f"Cycle #{cycle} 错误: {e}", exc_info=True)
        logger.info(f"等待 {interval}s...")
        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="独立社媒采集器")
    parser.add_argument("--loop", action="store_true", help="无限循环模式")
    parser.add_argument("--interval", type=int, default=600, help="循环间隔(秒)")
    args = parser.parse_args()

    if args.loop:
        asyncio.run(run_loop(args.interval))
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
