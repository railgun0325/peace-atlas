from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class ArticleInput(BaseModel):
    title: str = Field(min_length=1)
    url: HttpUrl | str
    source_name: str = Field(default="unknown")
    source_type: str = Field(default="local_media")
    published_at: datetime | None = None
    raw_text: str = Field(default="")
    language: str | None = None


class IngestRequest(BaseModel):
    articles: list[ArticleInput]


class IngestResponse(BaseModel):
    ok: bool
    inserted: int
    skipped_duplicates: int
    article_ids: list[int]


class AnalyzeWeeklyRequest(BaseModel):
    start_date: date
    end_date: date


class AnalyzeWeeklyResponse(BaseModel):
    ok: bool
    report_path: str
    summary: str


class GdeltFetchRequest(BaseModel):
    start_date: date
    end_date: date
    queries: list[str] | None = None
    max_records_per_query: int = Field(default=25, ge=1, le=250)
    ingest: bool = True


class GdeltFetchResponse(BaseModel):
    ok: bool
    fetched: int
    inserted: int = 0
    skipped_duplicates: int = 0
    articles: list[ArticleInput] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class DiscussionFetchRequest(BaseModel):
    start_date: date
    end_date: date
    platforms: list[str] = Field(default_factory=lambda: ["ptt", "reddit"])
    keywords: list[str] | None = None
    max_posts_per_platform: int = Field(default=20, ge=1, le=100)
    max_comments_per_post: int = Field(default=50, ge=0, le=300)
    ingest: bool = True


class DcardCookieRequest(BaseModel):
    cf_clearance: str
    cf_bm: str = ""
    oauth_token: str = ""
    user_agent: str = ""
    all_cookies: dict[str, str] = {}


class SourceRef(BaseModel):
    title: str
    url: str
    source_name: str
    source_type: str
    published_at: str | None = None


class StructuredAnalysisInput(BaseModel):
    week_start: str
    week_end: str
    event_clusters: list[dict[str, Any]]
    topic_changes: list[dict[str, Any]]
    entity_changes: list[dict[str, Any]]
    source_list: list[SourceRef]
    official_claims: list[dict[str, Any]]
    media_divergence: list[dict[str, Any]]
    historical_comparison: dict[str, Any]
    methodology_notes: list[str]
    discussion_signals: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# Japan anti-war intelligence models
# ═══════════════════════════════════════════════════════

class JapanSocialAccountCreate(BaseModel):
    platform: str = Field(min_length=1)
    username: str = Field(min_length=1)
    display_name: str | None = None
    account_url: str
    follower_count: int = 0
    bio: str | None = None
    category: str = "antiwar"
    tags: list[str] = Field(default_factory=list)


class JapanSocialAccountVerify(BaseModel):
    verified: bool
    evidence_url: str | None = None


class JapanSocialAccountDiscover(BaseModel):
    platform: str | None = None
    keywords: list[str] = Field(default_factory=list)
    category: str | None = None
    max_accounts: int = Field(default=20, ge=1, le=100)


class JapanSocialFetchRequest(BaseModel):
    start_date: date
    end_date: date
    platform: str | None = None
    platforms: list[str] | None = None
    keywords: list[str] = Field(default_factory=list)
    include_verified_accounts: bool = False
    include_candidate_accounts: bool = False
    account_ids: list[int] | None = None
    max_posts: int = Field(default=50, ge=1, le=500)


class JapanSocialSearchRequest(BaseModel):
    """Cross-platform keyword search request."""
    keywords: list[str] = Field(min_length=1)
    start_date: date
    end_date: date
    platforms: list[str] | None = None
    max_posts_per_platform: int = Field(default=20, ge=1, le=100)


class JapanNewsFetchRequest(BaseModel):
    start_date: date
    end_date: date
    keywords: list[str] | None = None
    categories: list[str] | None = None
    max_records: int = Field(default=50, ge=1, le=250)
    ingest: bool = True


class JapanAnalyzeRequest(BaseModel):
    start_date: date
    end_date: date
    include_social: bool = True
    include_news: bool = True
    include_events: bool = True
    model: str | None = None


class JapanResearchTaskCreate(BaseModel):
    task_type: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class JapanResearchTaskResponse(BaseModel):
    ok: bool
    task: dict[str, Any]


class JapanReportResponse(BaseModel):
    ok: bool
    reports: list[dict[str, Any]]
