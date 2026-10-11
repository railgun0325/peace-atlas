"""Resolve a public RSS article URL; never treat the aggregator as article text.

The public wire format is documented by the MIT-licensed original project:
https://github.com/SSujitX/google-news-url-decoder
This independent adapter uses the existing vetted transport, no credentials,
and rejects unknown or ambiguous responses instead of guessing an outlet URL.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from . import pipeline

ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
CONTEXT = [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
           "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0]


def article_id(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(?:rss/)?(?:articles|read)/([A-Za-z0-9_-]{16,2048})/?", parsed.path)
    return match.group(1) if parsed.scheme == "https" and parsed.hostname in {"news.google.com", "www.news.google.com"} and match else None


def parse_original_url(body):
    # Public responses can contain an XSSI prefix and length-framed JSON
    # blocks. Decode only complete top-level frames, never regex-extract URLs.
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    candidates = []
    decoder = json.JSONDecoder()
    frames = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            rows, end = decoder.raw_decode(line)
            if line[end:].strip() or not isinstance(rows, list):
                continue
            frames += 1
            if frames > 32:
                raise pipeline.PipelineError("聚合器地址解析响应超出帧预算。")
            for row in rows:
                if not (isinstance(row, list) and len(row) >= 3 and row[:2] == ["wrb.fr", "Fbv4je"]):
                    continue
                if row[-1] not in {"0", None}:
                    continue
                payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                if isinstance(payload, list) and len(payload) >= 2 and payload[0] == "garturlres" and isinstance(payload[1], str):
                    candidates.append(pipeline._url(payload[1]))
        except (ValueError, TypeError, UnicodeError):
            continue
    if len(candidates) != 1 or urlsplit(candidates[0]).hostname in {"news.google.com", "www.news.google.com", "accounts.google.com"}:
        raise pipeline.PipelineError("聚合器未返回唯一可核对的出版方地址，保留检索摘要。")
    return candidates[0]


async def resolve_original_url(url):
    opaque_id = article_id(url)
    if not opaque_id:
        raise pipeline.PipelineError("不是可解析的公开新闻聚合地址。")
    page_url = "https://news.google.com/rss/articles/" + opaque_id + "?hl=ja&gl=JP&ceid=JP%3Aja"
    body, final, _ = await pipeline._fetch_public_feed(page_url, accept="text/html")
    if urlsplit(final).hostname not in {"news.google.com", "www.news.google.com"}:
        raise pipeline.PipelineError("聚合器要求验证或转向其他页面，未继续地址解析。")
    node = BeautifulSoup(body, "html.parser").select_one('[data-n-a-sg][data-n-a-ts]')
    signature, timestamp = (str(node.get("data-n-a-sg", "")), str(node.get("data-n-a-ts", ""))) if node else ("", "")
    if not signature or len(signature) > 4096 or not re.fullmatch(r"\d{1,16}", timestamp):
        raise pipeline.PipelineError("聚合页缺少公开地址解析参数，未猜测出版方地址。")
    request = ["garturlreq", CONTEXT, opaque_id, int(timestamp), signature]
    envelope = [["Fbv4je", json.dumps(request, separators=(",", ":")), None, "0"]]
    data = urlencode({"f.req": json.dumps([envelope], separators=(",", ":"))}).encode("utf-8")
    reply, _, _ = await pipeline._fetch_public_feed(ENDPOINT, accept="application/json", post_data=data)
    return parse_original_url(reply)
