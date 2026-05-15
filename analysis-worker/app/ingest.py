from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .clean import clean_article_text, detect_language
from .db import get_conn
from .dedupe import content_fingerprint, normalize_url
from .embeddings import maybe_embed
from .entities import load_entity_config, match_entities
from .models import ArticleInput
from .qdrant_store import upsert_article
from .topics import classify_topics, load_topic_config


async def ingest_articles(articles: list[ArticleInput]) -> dict[str, Any]:
    entity_config = load_entity_config()
    topic_config = load_topic_config()
    inserted = 0
    skipped = 0
    article_ids: list[int] = []
    with get_conn() as conn:
        for article in articles:
            title = article.title.strip()
            url = normalize_url(str(article.url))
            clean_text = clean_article_text(title, article.raw_text)
            language = article.language or detect_language(clean_text)
            fingerprint = content_fingerprint(title, clean_text)
            fetched_at = datetime.now(timezone.utc)
            published_at = article.published_at or fetched_at
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM articles WHERE url = %s OR content_hash = %s LIMIT 1",
                    (url, fingerprint),
                )
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO articles
                      (title, url, source_name, source_type, published_at, fetched_at, language, raw_text, clean_text, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        title,
                        url,
                        article.source_name,
                        article.source_type,
                        published_at,
                        fetched_at,
                        language,
                        article.raw_text,
                        clean_text,
                        fingerprint,
                    ),
                )
                article_id = int(cur.fetchone()["id"])
                article_ids.append(article_id)
                inserted += 1
                entities = match_entities(f"{title}\n{clean_text}", entity_config)
                for entity in entities:
                    cur.execute(
                        """
                        INSERT INTO article_entities
                          (article_id, entity_text, entity_type, normalized_name, confidence)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            article_id,
                            entity["entity_text"],
                            entity["entity_type"],
                            entity["normalized_name"],
                            entity["confidence"],
                        ),
                    )
                topics = classify_topics(title, clean_text, topic_config)
                for topic in topics:
                    cur.execute(
                        """
                        INSERT INTO article_topics (article_id, topic_name, confidence)
                        VALUES (%s, %s, %s)
                        """,
                        (article_id, topic["topic_name"], topic["confidence"]),
                    )
            vector = await maybe_embed(f"{title}\n{clean_text[:4000]}")
            if vector:
                await upsert_article(
                    article_id,
                    vector,
                    {
                        "title": title,
                        "url": url,
                        "source_name": article.source_name,
                        "source_type": article.source_type,
                        "published_at": published_at.isoformat(),
                    },
                )
    return {"inserted": inserted, "skipped_duplicates": skipped, "article_ids": article_ids}
