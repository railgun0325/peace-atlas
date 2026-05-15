from __future__ import annotations

import os
from typing import Any

import httpx


COLLECTION = "japan_antiwar_articles"


def qdrant_url() -> str:
    return os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")


async def health() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{qdrant_url()}/healthz")
            return response.status_code < 500
    except Exception:
        return False


async def ensure_collection(vector_size: int) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{qdrant_url()}/collections/{COLLECTION}")
        if response.status_code == 200:
            return
        if response.status_code != 404:
            response.raise_for_status()
        payload = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        create = await client.put(f"{qdrant_url()}/collections/{COLLECTION}", json=payload)
        create.raise_for_status()


async def upsert_article(article_id: int, vector: list[float], payload: dict[str, Any]) -> None:
    if not vector:
        return
    try:
        await ensure_collection(len(vector))
        body = {"points": [{"id": article_id, "vector": vector, "payload": payload}]}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(
                f"{qdrant_url()}/collections/{COLLECTION}/points?wait=true",
                json=body,
            )
            response.raise_for_status()
    except Exception:
        return


async def retrieve_vectors(article_ids: list[int]) -> dict[int, list[float]]:
    if not article_ids:
        return {}
    try:
        body = {"ids": article_ids, "with_payload": False, "with_vector": True}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{qdrant_url()}/collections/{COLLECTION}/points", json=body)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            data = response.json()
        vectors: dict[int, list[float]] = {}
        for point in data.get("result", []):
            vector = point.get("vector")
            if isinstance(vector, dict):
                vector = vector.get("")
            if isinstance(vector, list):
                vectors[int(point["id"])] = [float(value) for value in vector]
        return vectors
    except Exception:
        return {}
