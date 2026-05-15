from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx


def chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def load_prompt() -> str:
    prompt_path = Path(os.getenv("PROMPT_PATH", "")).expanduser()
    candidates = [
        prompt_path if str(prompt_path) != "." else None,
        Path(__file__).resolve().parents[1] / "prompts" / "weekly_deep_analysis_prompt.md",
        Path(__file__).resolve().parents[2] / "prompts" / "weekly_deep_analysis_prompt.md",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return "请基于结构化输入生成中立、证据分层的台湾政治公开情报周报。"


def load_japan_prompt() -> str:
    prompt_path = os.getenv("JAPAN_PROMPT_PATH", "").strip()
    candidates = [
        Path(prompt_path) if prompt_path else None,
        Path(__file__).resolve().parents[1] / "prompts" / "japan_antiwar_analysis_prompt.md",
        Path(__file__).resolve().parents[2] / "prompts" / "japan_antiwar_analysis_prompt.md",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return load_prompt()


async def generate_weekly_report(structured_input: dict[str, Any]) -> str | None:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    system_prompt = load_prompt()
    user_payload = json.dumps(structured_input, ensure_ascii=False, indent=2, default=str)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下是已清洗、去重、聚类后的结构化公开来源输入。"
                    "请严格按系统提示输出 Markdown 周报，不要编造来源。\n\n"
                    f"{user_payload}"
                ),
            },
        ],
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_seconds = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "0"))
    timeout = (
        httpx.Timeout(None, connect=30.0, read=None, write=30.0, pool=None)
        if timeout_seconds <= 0
        else httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=30.0)
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(chat_endpoint(base_url), headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def generate_japan_report(structured_input: dict[str, Any]) -> str | None:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    system_prompt = load_japan_prompt()
    user_payload = json.dumps(structured_input, ensure_ascii=False, indent=2, default=str)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "以下はクレンジング・重複除去・クラスタリング済みの構造化された公開情報入力です。"
                    "システム指示に従い、Markdown 週報を出力してください。出典を捏造しないでください。\n\n"
                    "candidate（未検証）アカウントの投稿は主要な結論に含めず、素材リストのみに記載してください。\n\n"
                    f"{user_payload}"
                ),
            },
        ],
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout_seconds = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "0"))
    timeout = (
        httpx.Timeout(None, connect=30.0, read=None, write=30.0, pool=None)
        if timeout_seconds <= 0
        else httpx.Timeout(timeout_seconds, connect=30.0, read=timeout_seconds, write=30.0)
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(chat_endpoint(base_url), headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
