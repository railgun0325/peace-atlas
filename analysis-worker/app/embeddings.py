from __future__ import annotations

import os

import httpx


def _embedding_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


async def maybe_embed(text: str) -> list[float] | None:
    api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
    model = os.getenv("EMBEDDING_MODEL", "").strip()
    if not api_key or not base_url or not model:
        return None
    payload = {"model": model, "input": text[:8000]}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(_embedding_endpoint(base_url), json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            vector = data["data"][0]["embedding"]
            return [float(value) for value in vector]
    except Exception:
        return None
