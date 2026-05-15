from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
import math
from typing import Any


def article_signature(article: dict[str, Any]) -> str:
    return f"{article.get('title', '')} {article.get('clean_text', '')[:600]}".lower()


def similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    return SequenceMatcher(None, article_signature(left), article_signature(right)).ratio()


def evidence_grade(source_types: set[str], source_count: int) -> str:
    official_types = {"official", "official_government", "news_agency"}
    if source_count >= 3 and (official_types & source_types):
        return "A"
    if source_count >= 2 or (official_types & source_types):
        return "B"
    return "C"


def cluster_articles(articles: list[dict[str, Any]], threshold: float = 0.42) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for article in articles:
        target = None
        best_score = 0.0
        for cluster in clusters:
            score = max(similarity(article, existing) for existing in cluster["articles"])
            if score > best_score:
                best_score = score
                target = cluster
        if target and best_score >= threshold:
            target["articles"].append(article)
            target["similarity_scores"].append(best_score)
        else:
            clusters.append({"articles": [article], "similarity_scores": [1.0]})
    return _enrich_clusters(clusters, method="local_similarity")


def cluster_articles_by_vectors(
    articles: list[dict[str, Any]],
    vectors: dict[int, list[float]],
    threshold: float = 0.82,
) -> list[dict[str, Any]]:
    if len(vectors) < 2:
        return cluster_articles(articles)

    vector_articles = [article for article in articles if article.get("id") in vectors]
    missing_articles = [article for article in articles if article.get("id") not in vectors]
    clusters: list[dict[str, Any]] = []

    for article in vector_articles:
        article_vector = vectors[article["id"]]
        target = None
        best_score = 0.0
        for cluster in clusters:
            score = max(_cosine(article_vector, vectors[existing["id"]]) for existing in cluster["articles"])
            if score > best_score:
                best_score = score
                target = cluster
        if target and best_score >= threshold:
            target["articles"].append(article)
            target["similarity_scores"].append(best_score)
        else:
            clusters.append({"articles": [article], "similarity_scores": [1.0]})

    for article in missing_articles:
        clusters.append({"articles": [article], "similarity_scores": [1.0]})

    return _enrich_clusters(clusters, method="embedding_qdrant")


def _enrich_clusters(clusters: list[dict[str, Any]], method: str) -> list[dict[str, Any]]:
    enriched = [_cluster_summary(index, cluster, method) for index, cluster in enumerate(clusters, start=1)]
    return sorted(enriched, key=lambda item: (-item["article_count"], item["representative_title"]))


def _cluster_summary(index: int, cluster: dict[str, Any], method: str) -> dict[str, Any]:
    items = cluster["articles"]
    topics = Counter(topic for item in items for topic in item.get("topics", []))
    entities = Counter(entity["normalized_name"] for item in items for entity in item.get("entities", []))
    source_types = {item.get("source_type") or "unknown" for item in items}
    source_names = sorted({item.get("source_name") or "unknown" for item in items})
    published = [item.get("published_at") for item in items if item.get("published_at")]
    official_sources = [
        _source_ref(item)
        for item in items
        if (item.get("source_type") or "").lower() in {"official", "official_government"}
    ]
    party_sources = [
        _source_ref(item)
        for item in items
        if (item.get("source_type") or "").lower() in {"party", "official_party"}
    ]
    media_by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in items:
        media_by_type[item.get("source_type") or "unknown"].append(_source_ref(item))
    return {
        "cluster_id": index,
        "representative_title": items[0].get("title", "Untitled"),
        "article_count": len(items),
        "source_count": len(source_names),
        "source_names": source_names,
        "source_types": sorted(source_types),
        "evidence_grade": evidence_grade(source_types, len(source_names)),
        "dominant_topics": [name for name, _ in topics.most_common(5)],
        "entities": [name for name, _ in entities.most_common(10)],
        "first_seen": _iso(min(published)) if published else None,
        "last_seen": _iso(max(published)) if published else None,
        "official_sources": official_sources,
        "party_sources": party_sources,
        "media_by_type": dict(media_by_type),
        "sources": [_source_ref(item) for item in items],
        "avg_similarity": round(sum(cluster["similarity_scores"]) / len(cluster["similarity_scores"]), 3),
        "cluster_method": method,
    }


def _source_ref(article: dict[str, Any]) -> dict[str, str]:
    published = article.get("published_at")
    return {
        "title": article.get("title") or "",
        "url": article.get("url") or "",
        "source_name": article.get("source_name") or "",
        "source_type": article.get("source_type") or "",
        "published_at": _iso(published) if published else "",
    }


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
