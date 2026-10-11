"""Workbench v2 HTTP boundary: explicit contracts, errors and application life cycle."""
from __future__ import annotations

import hmac
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import pipeline
from .jobs import JobRunner
from .job_retries import RetryConflict
from .review import QueueArgs, BatchArgs, BatchApply, AutoReviewArgs, AutoReviewStart, ReviewConflict, review_queue, preview_batch as review_batch_preview, apply_batch as review_batch_apply, review_estimate, start_review
from .store import Store
from .backup import BackupService, BackupError
from .limits import (
    BATCH_RECORDS_DEFAULT, BATCH_RECORDS_MAX, COLLECT_RECORDS_DEFAULT, COLLECT_RECORDS_MAX,
    COLLECT_SCROLLS_DEFAULT, COLLECT_SCROLLS_MAX, RESEARCH_RECORDS_DEFAULT, RESEARCH_RECORDS_MAX,
    RESEARCH_SCROLLS_DEFAULT, RESEARCH_SCROLLS_MAX, RESEARCH_TIME_DEFAULT, RESEARCH_TIME_MIN,
    RESEARCH_TIME_MAX, RESEARCH_DEPTH_MAX, RESEARCH_ACCOUNTS_MAX, RESEARCH_EDGES_MAX, RESEARCH_PER_ACCOUNT_MAX, RESEARCH_NEIGHBORS_MAX, budget_limits,
)
from .social.browser_bridge import BrowserBridge, BridgeError, EXTENSION_ORIGIN

STATIC = Path(__file__).resolve().parents[1] / "static" / "workbench"
log = logging.getLogger(__name__)


class ImportRequest(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class JobRequest(BaseModel):
    kind: Literal["collect", "report", "login", "research"]
    params: dict[str, Any]
    preview_token: str = Field(default="", max_length=250)


class AccountCheckRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list, max_length=20)
    source_url: str = Field(default="", max_length=2048)
    force: bool = False


class AccountIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["organization", "individual", "unknown"]
    manual_locked: bool = True
    note: str = Field(default="", max_length=1000)
    evidence_urls: list[str] = Field(default_factory=list, max_length=5)


class VerificationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)
    label: str = Field(default="", max_length=200)
    account_type: Literal["organization", "individual", "unknown"] = "unknown"


class VerificationSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[VerificationSource] = Field(max_length=5)


class AccountRelevanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["included", "irrelevant", "unknown"]
    reason: str = Field(default="", max_length=1000)


class ArticleReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    original_url: str = Field(default="", max_length=2048)


class ActivityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = ""
    status: Literal["candidate", "confirmed", "rejected", "inconclusive"] = "confirmed"
    title: str = Field(min_length=1, max_length=260)
    date: str = ""
    prefecture: str = ""
    quote: str = Field(min_length=1, max_length=2000)
    merge_with: str = ""
    note: str = Field(default="", max_length=1600)


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["trash", "restore", "purge"]
    preview_digest: str = ""
    reason: str = Field(default="", max_length=500)
    confirm_purge: bool = False


class Period(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期。")
        if (self.end_date - self.start_date).days > 3660:
            raise ValueError("单次时间范围不能超过十年。")
        return self


class CollectParams(Period):
    model_config = ConfigDict(extra="forbid")
    source: Literal["rss", "google_news", "twitter", "instagram"]
    keyword: str = Field(default="", max_length=1200)
    keyword_mode: Literal["any", "all"] = "any"
    feed_url: str = Field(default="", max_length=2000)
    max_records: int = Field(default=COLLECT_RECORDS_DEFAULT, ge=1, le=COLLECT_RECORDS_MAX)
    mode: Literal["keyword", "account", "hashtag"] = "keyword"
    handle: str = Field(default="", max_length=50)
    max_accounts: int = Field(default=3, ge=1, le=10)
    max_scrolls: int = Field(default=COLLECT_SCROLLS_DEFAULT, ge=1, le=COLLECT_SCROLLS_MAX)
    include_following: bool = False
    max_following: int = Field(default=20, ge=0, le=50)
    relevance: Literal["focused", "broad"] = "focused"

    @model_validator(mode="after")
    def valid_source(self):
        if self.source == "rss" and not self.feed_url.strip():
            raise ValueError("RSS 采集需要订阅地址。")
        if self.source in {"twitter", "instagram"}:
            self.keyword = self.keyword.strip()
            self.handle = self.handle.strip().lstrip("@").lower()
            if self.mode == "account":
                pattern = r"[a-z0-9_]{1,15}" if self.source == "twitter" else r"[a-z0-9_.]{1,30}"
                if not re.fullmatch(pattern, self.handle):
                    raise ValueError("请填写有效的账号用户名，不含网址或 @。")
            elif not self.keyword:
                raise ValueError("社媒检索需要关键词或标签。")
            if self.mode == "hashtag" and re.search(r"\s", self.keyword):
                raise ValueError("标签不能包含空白；多个关键词请使用关键词模式。")
        if self.keyword.strip() and self.mode != "account":
            from .query_plan import build_query_plan
            build_query_plan(self.model_dump(mode="json"))
        return self


class ResearchParams(Period):
    model_config = ConfigDict(extra="forbid")
    source: Literal["google_news", "twitter", "instagram"] = "google_news"
    seed_sources: list[Literal["google_news", "twitter", "instagram"]] = Field(default_factory=lambda: ["google_news"], min_length=1, max_length=3)
    keyword: str = Field(default="", max_length=1200)
    keyword_mode: Literal["any", "all"] = "any"
    research_goal: str = Field(default="", max_length=1200)
    mode: Literal["keyword", "account", "hashtag"] = "keyword"
    handle: str = Field(default="", max_length=50)
    seed_accounts: list[str] = Field(default_factory=list, max_length=10)
    depth: int = Field(default=2, ge=1, le=RESEARCH_DEPTH_MAX)
    max_accounts: int = Field(default=0, ge=0, le=RESEARCH_ACCOUNTS_MAX)
    max_edges: int = Field(default=0, ge=0, le=RESEARCH_EDGES_MAX)
    max_records: int = Field(default=0, ge=0, le=RESEARCH_RECORDS_MAX)
    max_scrolls: int = Field(default=RESEARCH_SCROLLS_DEFAULT, ge=1, le=RESEARCH_SCROLLS_MAX)
    time_budget_seconds: int = Field(default=0, ge=RESEARCH_TIME_MIN, le=RESEARCH_TIME_MAX)
    max_records_per_account: int = Field(default=1000, ge=1, le=RESEARCH_PER_ACCOUNT_MAX)
    max_neighbors_per_account: int = Field(default=100, ge=1, le=RESEARCH_NEIGHBORS_MAX)
    expand_relations: list[Literal["following", "followers", "mentions"]] = Field(default_factory=lambda: ["following", "mentions"], max_length=3)
    analysis_mode: Literal["auto", "rules", "ai"] = "auto"
    relevance: Literal["focused", "broad"] = "focused"
    resume_run_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_research(self):
        if 0 < self.time_budget_seconds < 30:
            raise ValueError("研究时长须为 0 或至少 30 秒。")
        self.seed_sources = list(dict.fromkeys(self.seed_sources))
        self.expand_relations = list(dict.fromkeys(self.expand_relations))
        self.handle = self.handle.strip().lstrip("@").lower()
        if self.source not in self.seed_sources:
            raise ValueError("主要来源必须包含在研究入口中。")
        if self.mode == "account":
            pattern = r"[a-z0-9_]{1,15}" if self.source == "twitter" else r"[a-z0-9_.]{1,30}"
            if self.source == "google_news" or not re.fullmatch(pattern, self.handle):
                raise ValueError("账号研究需要有效的 X 或 Instagram 用户名。")
        elif not self.keyword.strip():
            raise ValueError("请填写研究关键词；研究目标用于进一步说明关注方向。")
        from .research_engine import normalize_research_seeds
        roots = normalize_research_seeds(self.model_dump(mode="json"))
        if self.max_accounts and len(roots) > self.max_accounts:
            raise ValueError("最多探索账号不能少于明确指定的研究起点数量。")
        primary_url = roots[0] if self.mode == "account" else None
        self.seed_accounts = [url for url in roots if url != primary_url]
        if self.keyword.strip():
            from .query_plan import build_query_plan
            build_query_plan(self.model_dump(mode="json"))
        return self


class ResearchContinuationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(default="", max_length=250)
    max_accounts: int | None = Field(default=None, ge=0, le=RESEARCH_ACCOUNTS_MAX, strict=True)
    max_edges: int | None = Field(default=None, ge=0, le=RESEARCH_EDGES_MAX, strict=True)
    max_records: int | None = Field(default=None, ge=0, le=RESEARCH_RECORDS_MAX, strict=True)
    time_budget_seconds: int | None = Field(default=None, ge=RESEARCH_TIME_MIN, le=RESEARCH_TIME_MAX, strict=True)


class QueryPlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1, max_length=1200)
    keyword_mode: Literal["any", "all"] = "any"
    source: Literal["rss", "google_news", "twitter", "instagram"] = "google_news"
    mode: Literal["keyword", "account", "hashtag"] = "keyword"
    seed_sources: list[Literal["google_news", "twitter", "instagram"]] = Field(default_factory=list, max_length=3)
    use_ai: bool = False
    research_goal: str = Field(default="", max_length=1200)
    start_date: date | None = None
    end_date: date | None = None


class DraftParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = Field(default=1, alias="schema")
    values: dict[str, str | int | bool] = Field(default_factory=dict)
    modeBySource: dict[str, Literal["keyword", "account", "hashtag"]] = Field(default_factory=dict)
    updated_at: str = Field(default="", max_length=64)
    revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def safe_fields(self):
        allowed = {"source", "mode", "workflow", "keyword", "keyword_mode", "research_goal", "handle", "seed_accounts", "feed_url", "start_date", "end_date", "max_records", "relevance", "max_accounts", "max_scrolls", "include_following", "max_following", "depth", "max_edges", "time_budget_seconds", "analysis_mode", "seed_news", "seed_twitter", "seed_instagram", "expand_following", "expand_followers", "expand_mentions", "research_max_accounts", "research_max_scrolls", "max_records_per_account", "max_neighbors_per_account", "batch_kind", "total_records", "batch_title"}
        if set(self.values) - allowed or set(self.modeBySource) - {"twitter", "instagram"}:
            raise ValueError("草稿只能保存当前采集表单字段。")
        if any(isinstance(value, str) and len(value) > 2400 for value in self.values.values()):
            raise ValueError("草稿字段超出允许长度。")
        return self


class DraftDeleteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int | None = Field(default=None, ge=0)


class ClaimReviewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["confirmed", "rejected", "inconclusive"]
    note: str = Field(default="", max_length=1600)
    evidence_url: str = Field(default="", max_length=2000)


class BatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="批量公开资料研究", max_length=160)
    kind: Literal["research", "collect"] = "research"
    params: dict[str, Any]
    total_records: int = Field(default=BATCH_RECORDS_DEFAULT, ge=1, le=BATCH_RECORDS_MAX)
    preview_token: str = Field(default="", max_length=250)


class LoginParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["twitter", "instagram"]
    method: Literal["manual", "firefox", "auto"] = "auto"
    profile_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_method(self):
        if self.method == "firefox":
            if self.platform != "twitter" or not self.profile_id:
                raise ValueError("Firefox 会话复用仅支持 X，且必须明确选择配置。")
        elif self.profile_id is not None:
            raise ValueError("手动登录或自动会话检查不接受 Firefox 配置参数。")
        return self


class BrowserOpenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["twitter", "instagram"]


class BridgePairParams(BrowserOpenParams):
    code: str = Field(pattern=r"^[0-9a-f]{64}$")


class BridgeSyncParams(BrowserOpenParams):
    cookies: list[dict[str, Any]] = Field(min_length=2, max_length=3)


class ReportParams(Period):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="日本和平运动研究简报", min_length=1, max_length=160)
    mode: Literal["evidence", "ai"] = "evidence"
    analysis_harness: Literal["off", "dsh"] = "off"
    harness_max_model_calls: int = Field(default=24, ge=0, le=4096, strict=True)
    harness_max_tool_calls: int = Field(default=64, ge=0, le=65536, strict=True)
    include_news: bool = True
    include_social: bool = True
    read_original: bool = True
    research_run_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    time_budget_seconds: int = Field(default=0, ge=0, le=RESEARCH_TIME_MAX, strict=True)

    @model_validator(mode="after")
    def valid_sources(self):
        if 0 < self.time_budget_seconds < 60:
            raise ValueError("报告时长须为 0 或至少 60 秒。")
        if self.harness_max_model_calls == 1 or self.harness_max_tool_calls == 1:
            raise ValueError("DSH 次数须为 0 或至少 2；0 表示不额外按次数截断。")
        if not self.include_news and not self.include_social:
            raise ValueError("至少选择一种资料类型。")
        if self.mode != "ai" and self.analysis_harness != "off":
            raise ValueError("DSH 工具分析仅适用于 AI 报告模式。")
        return self


class AccountData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=160)
    handle: str = Field(default="", max_length=200)
    platform: Literal["twitter", "instagram", "mastodon", "youtube", "rss", "other"] = "other"
    url: str = Field(default="", max_length=2000)
    organization: str = Field(default="", max_length=200)
    status: Literal["candidate", "verified", "excluded"] = "candidate"
    evidence_url: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class ReportRetryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_mode: Literal["original", "latest"] | None = None
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    analysis_harness: Literal["off", "dsh"] | None = None
    harness_max_model_calls: int | None = Field(default=None, ge=0, le=4096, strict=True)
    harness_max_tool_calls: int | None = Field(default=None, ge=0, le=65536, strict=True)
    time_budget_seconds: int | None = Field(default=None, ge=0, le=RESEARCH_TIME_MAX, strict=True)

    @model_validator(mode="after")
    def valid_optional_calls(self):
        if self.time_budget_seconds is not None and 0 < self.time_budget_seconds < 60:
            raise ValueError("报告时长须为 0 或至少 60 秒。")
        if self.harness_max_model_calls == 1 or self.harness_max_tool_calls == 1:
            raise ValueError("DSH 次数须为 0 或至少 2。")
        return self


class VerifyRequest(BaseModel):
    verified: bool
    evidence_url: str = ""


class ConnectionSettings(BaseModel):
    base_url: str = Field(max_length=2000)
    model: str = Field(min_length=1, max_length=160)
    api_key: str = Field(max_length=4096)


def fail(message: str, status: int = 400, code: str = "invalid_request"):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(store: Store | None = None, *, run_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store()
        app.state.runner = JobRunner(app.state.store)
        app.state.browser_bridge = BrowserBridge()
        app.state.backups = BackupService()
        if run_worker:
            await app.state.runner.start()
        yield
        if run_worker:
            await app.state.runner.stop()

    app = FastAPI(title="反戦 / Japan Peace Workbench", version="2.0.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        bridge_path = request.url.path in {"/api/v2/social/bridge/pair", "/api/v2/social/bridge/sync"}
        extension_origin = request.headers.get("origin", "")
        bridge_request = bridge_path and request.method in {"POST", "OPTIONS"}
        cors_headers = {}
        if bridge_request:
            # Only these two routes accept an extension origin; each handler
            # independently checks a one-use grant or its bound bridge token.
            if not EXTENSION_ORIGIN.fullmatch(extension_origin):
                return fail("连接必须来自本机的会话扩展。", 403, "bridge_origin_denied")
            cors_headers = {"Access-Control-Allow-Origin": extension_origin, "Vary": "Origin"}
            if request.method == "OPTIONS":
                requested_headers = {part.strip().lower() for part in request.headers.get("access-control-request-headers", "").split(",") if part.strip()}
                if request.headers.get("access-control-request-method") != "POST" or not requested_headers <= {"content-type", "x-workbench-bridge"}:
                    return fail("不支持的浏览器连接请求。", 403, "bridge_preflight_denied")
                return Response(status_code=204, headers={**cors_headers,
                    "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "Content-Type, X-Workbench-Bridge",
                    "Access-Control-Max-Age": "300", "Cache-Control": "no-store"})
            declared_length = request.headers.get("content-length")
            if declared_length is not None and (not re.fullmatch(r"[0-9]{1,5}", declared_length) or int(declared_length) > 32768):
                return fail("会话请求超出允许大小。", 413, "bridge_body_limit")
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return fail("会话请求必须使用 JSON。", 415, "bridge_content_type")
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 32768:
                    return fail("会话请求超出允许大小。", 413, "bridge_body_limit")
                chunks.append(chunk)
            # Starlette's body cache lets the downstream model consume the
            # bounded request. Never log or persist this transient payload.
            request._body = b"".join(chunks)
        # Reject cross-site mutations; desktop adds a random session cookie too.
        if not bridge_request and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return fail("不允许来自其他站点的操作。", 403, "origin_denied")
        token = os.getenv("WORKBENCH_SESSION_TOKEN", "")
        if token and request.url.path.startswith("/api/") and not bridge_request:
            supplied = request.cookies.get("wb_session") or request.headers.get("x-workbench-token", "")
            if not hmac.compare_digest(supplied, token):
                return fail("应用会话失效，请重新打开应用。", 401, "session_required")
        response = await call_next(request)
        response.headers.update(cors_headers)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [" / ".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors()]
        return fail("；".join(details), 422, "validation_error")

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return fail(str(exc), 422, "validation_error")

    @app.exception_handler(ReviewConflict)
    async def review_conflict(request, exc):
        return fail(str(exc), 409, 'review_conflict')

    @app.exception_handler(LookupError)
    async def not_found(request, exc):
        return fail("所请求的记录不存在。", 404, "not_found")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return fail(str(exc.detail), exc.status_code, "request_error")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.exception("Request failed: %s", request.url.path, exc_info=exc)
        return fail("操作未完成，数据服务发生错误。请重试或查看本机日志。", 500, "internal_error")

    def db(request: Request) -> Store:
        return request.app.state.store

    @app.get("/")
    async def index(request: Request, desktop_token: str = ""):
        token = os.getenv("WORKBENCH_SESSION_TOKEN", "")
        if desktop_token:
            if not token or not hmac.compare_digest(desktop_token, token):
                return fail("无效的启动会话。", 401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie("wb_session", token, httponly=True, samesite="strict")
            return response
        return FileResponse(STATIC / "index.html")

    app.mount("/assets", StaticFiles(directory=str(STATIC), check_dir=False), name="assets")

    @app.get("/health")
    @app.get("/api/v2/health")
    def health(request: Request):
        db(request).list_items(page_size=1)
        return {"ok": True, "version": "2.0.0", "status": "ready", "storage": "postgresql" if db(request)._postgres else "sqlite", "timezone": "Asia/Tokyo"}

    @app.get("/api/v2/capabilities")
    def capabilities(request: Request):
        postgres = db(request)._postgres
        return {**pipeline.capabilities(), "timezone": "Asia/Tokyo", "research": {"available": True, "max_depth": RESEARCH_DEPTH_MAX, "max_accounts": RESEARCH_ACCOUNTS_MAX, "max_edges": RESEARCH_EDGES_MAX, "optional_stops": True, "max_records": RESEARCH_RECORDS_MAX, "max_scrolls": RESEARCH_SCROLLS_MAX, "time_budget_seconds": RESEARCH_TIME_MAX, "autosave": True}, "budget_limits": budget_limits(), "storage": {"engine": "postgresql" if postgres else "sqlite"}, "backup": {"available": not postgres, "detail": "当前使用 PostgreSQL，请使用数据库的备份工具。" if postgres else "运行中也可备份全部研究资料、账号、报告、任务与草稿；不包含登录会话及 AI 密钥。"}}

    @app.get("/api/v2/backup")
    def download_backup(request: Request):
        try:
            return request.app.state.backups.response(db(request))
        except BackupError as exc:
            return fail(str(exc), exc.status, exc.code)

    @app.post("/api/v2/research/estimate")
    def research_estimate(payload: ResearchParams):
        from .research_engine import build_research_plan
        from .research_estimate import estimate_research
        values = payload.model_dump(mode="json")
        if values.get("resume_run_id"):
            raise ValueError("新研究预估不能带续查编号。")
        return estimate_research(values, query_count=len(build_research_plan(values).get("queries") or []),
                                 ai_configured=pipeline.capabilities()["llm"]["configured"])

    @app.post("/api/v2/research/plan")
    async def research_plan(payload: QueryPlanParams):
        from .query_plan import suggest_keywords
        values = payload.model_dump(mode="json", exclude_none=True)
        return await suggest_keywords(values)

    def batch_plan(payload):
        from .batch_plan import build_batch_plan
        model = ResearchParams if payload.kind == "research" else CollectParams
        params = model.model_validate(payload.params).model_dump(mode="json")
        if params.get("resume_run_id"):
            raise ValueError("新任务串不能带续查编号。")
        if params.get("analysis_mode") == "ai" and not pipeline.capabilities()["llm"]["configured"]:
            raise ValueError("尚未配置 AI 连接，可选择自动或规则模式。")
        plan = build_batch_plan(payload.title, payload.kind, params, payload.total_records)
        if payload.kind == "research":
            from .research_estimate import estimate_research, confirmation_token
            from .research_engine import build_research_plan
            estimates = [estimate_research(task["params"], query_count=len(build_research_plan(task["params"]).get("queries") or []),
                         ai_configured=pipeline.capabilities()["llm"]["configured"]) for task in plan["tasks"]]
            plan["estimate"] = {"lower_seconds": sum(row["lower_seconds"] for row in estimates),
                                "upper_seconds": sum(row["upper_seconds"] for row in estimates),
                                "basis": "任务依次执行；合计参数情景范围，尚未由本机实测校准。",
                                "confidence": "low"}
            plan["preview_token"] = confirmation_token({"title": payload.title, "kind": payload.kind, "params": params, "total_records": payload.total_records})
        return plan

    @app.post("/api/v2/research/batch-plan")
    def preview_batch(payload: BatchParams):
        return batch_plan(payload)

    @app.post("/api/v2/jobs/batch", status_code=202)
    def create_batch(request: Request, payload: BatchParams):
        plan = batch_plan(payload)
        if payload.kind == "research":
            from .research_estimate import validate_confirmation
            validate_confirmation({"title": payload.title, "kind": payload.kind, "params": ResearchParams.model_validate(payload.params).model_dump(mode="json"), "total_records": payload.total_records}, payload.preview_token)
        batch = db(request).create_job_batch(plan["title"], plan["tasks"], plan["total_records"])
        return {"batch": batch, "jobs": batch["jobs"], "plan": plan}

    @app.get("/api/v2/job-batches")
    def batches(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return db(request).list_job_batches(page=page, page_size=page_size)

    @app.get("/api/v2/job-batches/{batch_id}")
    def get_batch(request: Request, batch_id: str):
        batch = db(request).get_job_batch(batch_id)
        if batch is None:
            raise LookupError(batch_id)
        return batch

    @app.post("/api/v2/job-batches/{batch_id}/cancel")
    def cancel_batch(request: Request, batch_id: str):
        return db(request).cancel_job_batch(batch_id)

    @app.post("/api/v2/job-batches/{batch_id}/retry", status_code=202)
    def retry_batch(request: Request, batch_id: str):
        try:
            batch = db(request).retry_job_batch(batch_id)
        except RetryConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"batch": batch, "jobs": batch["jobs"]}

    def draft_response(record):
        return {"draft": {**record["data"], "revision": record["revision"], "updated_at": record["updated_at"]} if record else None}

    @app.get("/api/v2/drafts/collection")
    def get_collection_draft(request: Request):
        return draft_response(db(request).get_draft("collection"))

    @app.put("/api/v2/drafts/collection")
    def save_collection_draft(request: Request, payload: DraftParams):
        from .research_repository import DraftConflict
        try:
            result = db(request).save_draft("collection", payload.model_dump(by_alias=True, exclude={"revision"}), expected_revision=payload.revision, version=1)
        except DraftConflict:
            return fail("另一窗口已更新草稿。当前输入保留在本机，请重新同步后选择要保留的内容。", 409, "draft_conflict")
        return draft_response(result)

    @app.delete("/api/v2/drafts/collection")
    def clear_collection_draft(request: Request, payload: DraftDeleteParams | None = None):
        from .research_repository import DraftConflict
        try:
            db(request).clear_draft("collection", expected_revision=payload.revision if payload else None)
        except DraftConflict:
            return fail("另一窗口已更新草稿，未清除其输入。", 409, "draft_conflict")
        return {"cleared": True}

    def research_id(request, value):
        run_id = db(request).resolve_research_id(value)
        if run_id:
            return run_id
        raise LookupError(value)

    @app.get("/api/v2/research")
    def research_runs(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return db(request).list_research_runs(page=page, page_size=page_size)

    @app.get("/api/v2/research/{run_id}")
    def research_graph(request: Request, run_id: str, query: str = "", platform: str = "", relationship: str = "", status: str = "", max_depth: int | None = Query(None, ge=0, le=RESEARCH_DEPTH_MAX)):
        return db(request).research_graph(research_id(request, run_id), query=query, platform=platform, relationship=relationship, status=status, max_depth=max_depth, limit=300)

    @app.get("/api/v2/research/{run_id}/export")
    def research_export(request: Request, run_id: str):
        import json
        graph = db(request).export_research(research_id(request, run_id))
        return Response(json.dumps(graph, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="peace-atlas-research-{graph["run"]["id"]}.json"'})

    def continuation_preview_values(request, run_id, payload):
        run = db(request).get_research_run(research_id(request, run_id))
        params = {key: value for key, value in run["params"].items() if key in ResearchParams.model_fields and key != "resume_run_id"}
        limits = payload.model_dump(exclude_none=True, exclude={"preview_token"})
        params.update(limits)
        values = ResearchParams.model_validate(params).model_dump(mode="json")
        return run, values, limits

    @app.post("/api/v2/research/{run_id}/continue-estimate")
    def continuation_estimate(request: Request, run_id: str, payload: ResearchContinuationParams):
        from .research_engine import build_research_plan
        from .research_estimate import estimate_research, confirmation_token
        run, values, limits = continuation_preview_values(request, run_id, payload)
        result = estimate_research(values, query_count=len(build_research_plan(values).get("queries") or []),
                                   ai_configured=pipeline.capabilities()["llm"]["configured"])
        result["basis"] += " 续查沿用保存范围，已完成工作复用；待执行前沿可能变化。"
        result["preview_token"] = confirmation_token({"run_id": run["id"], "current_job_id": run["current_job_id"], "limits": limits})
        return result

    @app.post("/api/v2/research/{run_id}/continue", status_code=202)
    def continue_research(request: Request, run_id: str, payload: ResearchContinuationParams):
        try:
            from .research_estimate import validate_confirmation
            run, values, limits = continuation_preview_values(request, run_id, payload)
            if run["status"] in {"queued", "running"}:
                raise RetryConflict("该研究已有待执行或运行中的任务，请等待完成或先取消。")
            validate_confirmation({"run_id": run["id"], "current_job_id": run["current_job_id"], "limits": limits}, payload.preview_token)
            return db(request).continue_research_job(run_id, limits)
        except RetryConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v2/research/{run_id}/report")
    def research_report(request: Request, run_id: str):
        from .research_export import research_markdown
        graph = db(request).export_research(research_id(request, run_id))
        return Response(research_markdown(graph), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="peace-atlas-research-{graph["run"]["id"]}.md"'})

    @app.post("/api/v2/research/{run_id}/claims/{claim_id}/review")
    def review_claim(request: Request, run_id: str, claim_id: str, payload: ClaimReviewParams):
        return db(request).review_research_claim(research_id(request, run_id), claim_id, **payload.model_dump())

    @app.get('/api/v2/reviews/queue')
    def pending_reviews(request: Request, kind: Literal['accounts', 'claims', 'activities'] = 'claims',
                        pending_only: bool = True, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=200)):
        return review_queue(db(request), kind=kind, pending_only=pending_only, page=page, page_size=page_size)

    @app.post('/api/v2/reviews/batch/preview')
    def preview_reviews(request: Request, payload: BatchArgs):
        return review_batch_preview(db(request), payload.model_dump())

    @app.post('/api/v2/reviews/batch/apply')
    def apply_reviews(request: Request, payload: BatchApply):
        return review_batch_apply(db(request), payload.model_dump(exclude={'preview_digest'}), expected_digest=payload.preview_digest)

    @app.get('/api/v2/reviews/{job_id}/export')
    def export_reviews(request: Request, job_id: str):
        import json
        job = db(request).get_job(job_id)
        if not job or job['params'].get('_evidence_review') is not True:
            raise LookupError('自动审核任务不存在。')
        return Response(json.dumps(job, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="review-{job["id"]}.json"'})

    @app.post('/api/v2/reviews/estimate')
    def estimate_reviews(request: Request, payload: AutoReviewArgs):
        return review_estimate(db(request), payload.model_dump())

    @app.post('/api/v2/reviews/start', status_code=201)
    def automatic_reviews(request: Request, payload: AutoReviewStart):
        caps = pipeline.capabilities()
        if not caps['llm']['configured'] or not caps['analysis_harness']['available']:
            raise ValueError('请先保存 AI 连接，并使用包含 DSH 运行时的完整桌面版本。')
        job = start_review(db(request), payload.model_dump(exclude={'input_digest'}), expected_digest=payload.input_digest)
        return job

    @app.get("/api/v2/social")
    def social_capabilities():
        from . import social
        return social.capabilities()

    @app.get("/api/v2/social/firefox-profiles")
    def firefox_profiles():
        from .social.firefox_import import list_firefox_profiles
        return list_firefox_profiles()

    @app.get("/api/v2/social/browser")
    def external_browser_capabilities():
        from .social.external_browser import capabilities
        return capabilities()

    @app.post("/api/v2/social/browser/open")
    def open_external_browser(payload: BrowserOpenParams):
        from .social.external_browser import BrowserOpenError, open_platform
        try:
            return open_platform(payload.platform)
        except BrowserOpenError as exc:
            return fail(str(exc), exc.status, exc.code)

    @app.post("/api/v2/social/bridge/start")
    def start_browser_bridge(request: Request, payload: BrowserOpenParams):
        try:
            return request.app.state.browser_bridge.start(payload.platform, str(request.base_url).rstrip("/"))
        except BridgeError as exc:
            return fail(str(exc), exc.status, exc.code)

    @app.post("/api/v2/social/bridge/pair")
    def pair_browser_bridge(request: Request, payload: BridgePairParams):
        try:
            return request.app.state.browser_bridge.pair(payload.code, payload.platform, request.headers.get("origin", ""))
        except BridgeError as exc:
            return fail(str(exc), exc.status, exc.code)

    @app.post("/api/v2/social/bridge/sync")
    async def sync_browser_bridge(request: Request, payload: BridgeSyncParams):
        try:
            return await request.app.state.browser_bridge.sync(request.headers.get("x-workbench-bridge", ""),
                payload.platform, request.headers.get("origin", ""), payload.cookies)
        except BridgeError as exc:
            return fail(str(exc), exc.status, exc.code)

    @app.put("/api/v2/connection")
    def configure_connection(request: Request, payload: ConnectionSettings):
        if not os.getenv("WORKBENCH_SESSION_TOKEN"):
            raise HTTPException(403, "请使用桌面应用安全保存 AI 连接。")
        address = urlsplit(payload.base_url)
        if address.scheme != "https" or not address.hostname or address.username or address.password:
            raise ValueError("AI 服务地址必须为不含凭据的 HTTPS 地址。")
        if db(request).overview()["stats"]["active_jobs"]:
            raise HTTPException(409, "请等待运行中及排队任务结束后再修改 AI 连接。")
        os.environ["DEEPSEEK_BASE_URL"] = payload.base_url.rstrip("/")
        os.environ["DEEPSEEK_MODEL"] = payload.model
        os.environ["DEEPSEEK_API_KEY"] = payload.api_key
        return {"configured": bool(payload.api_key), "model": payload.model}

    @app.post("/api/v2/lifecycle/shutdown")
    async def desktop_shutdown(request: Request):
        callback = getattr(request.app.state, "shutdown_callback", None)
        if not os.getenv("WORKBENCH_SESSION_TOKEN") or callback is None:
            raise HTTPException(404, "此接口仅供桌面应用退出。")
        callback()
        return {"ok": True}

    @app.get("/api/v2/overview")
    def overview(request: Request):
        return db(request).overview()

    @app.get("/api/v2/items")
    def items(request: Request, query: str = "", type: str = "", topic: str = "", source: str = "", start_date: str = "", end_date: str = "", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), trash: bool = False):
        if start_date and end_date:
            Period(start_date=start_date, end_date=end_date)
        return db(request).list_items(query=query, type=type, topic=topic, source=source, start_date=start_date, end_date=end_date, page=page, page_size=page_size, trash=trash)

    @app.post("/api/v2/items/cleanup/preview")
    def cleanup_preview(request: Request, payload: CleanupRequest):
        return db(request).cleanup_preview(payload.ids, payload.action)

    @app.post("/api/v2/items/cleanup/apply")
    def cleanup_apply(request: Request, payload: CleanupRequest):
        return db(request).cleanup_apply(payload.ids, payload.action, payload.preview_digest, payload.reason, payload.confirm_purge)

    @app.get("/api/v2/items/{item_id}")
    def item(request: Request, item_id: str):
        result = db(request).get_item(item_id)
        if result is None:
            raise LookupError(item_id)
        return result

    @app.get("/api/v2/items/{item_id}/revisions")
    def item_revisions(request: Request, item_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
        return db(request).item_revisions(item_id, page=page, page_size=page_size)

    @app.post("/api/v2/items/{item_id}/read-original", status_code=202)
    async def read_original(request: Request, item_id: str, payload: ArticleReadRequest):
        store = db(request)
        selected = store.get_item(item_id)
        if not selected:
            raise LookupError(item_id)
        if selected["type"] != "news" or (selected.get("metadata") or {}).get("atlas_cleanup", {}).get("status") == "trashed":
            raise ValueError("只可读取当前资料库的新闻原文。")
        original_url = pipeline._url(payload.original_url) if payload.original_url else ""
        with store._transaction(write=True) as connection:
            if store._postgres:
                store._execute(connection, "SELECT pg_advisory_xact_lock(1278032975)")
            suffix = " FOR UPDATE" if store._postgres else ""
            active = store._execute(connection, "SELECT * FROM wb_jobs WHERE kind='collect' AND status IN ('queued','running')" + suffix).fetchall()
            for row in active:
                existing = store._job(row)
                if existing["params"].get("_news_capture") and existing["params"].get("item_id") == item_id:
                    return {**existing, "reused": True}
            created = store.create_job("collect", {"_news_capture": True, "item_id": item_id, "original_url": original_url}, _connection=connection)
        request.app.state.runner.wake.set()
        return created

    @app.get("/api/v2/activities")
    def activities(request: Request, start_date: str = "", end_date: str = "", status: Literal["", "confirmed", "candidate"] = "", prefecture: str = "", page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
        return db(request).activities(start_date, end_date, page, page_size, status, prefecture)

    @app.post("/api/v2/items/{item_id}/activities")
    def activity_review(request: Request, item_id: str, payload: ActivityReviewRequest):
        return db(request).review_activity(item_id, payload.model_dump())

    @app.post("/api/v2/items/import")
    def import_items(request: Request, payload: ImportRequest):
        normalized, issues = [], []
        for i, item in enumerate(payload.items):
            try:
                # Imported material cannot assert its own verified status.
                clean = {**item, "account_status": "candidate", "evidence_url": ""}
                normalized.append(pipeline.enrich_item(clean))
            except (ValueError, TypeError) as exc:
                issues.append({"index": i, "message": str(exc)})
        result = db(request).import_items(normalized) if normalized else {"inserted": 0, "duplicates": 0, "rejected": 0, "issues": [], "ids": []}
        result["issues"].extend(issues)
        result["rejected"] += len(issues)
        return result

    @app.get("/api/v2/accounts")
    def accounts(request: Request):
        from .account_identity import project_account
        return {"items": [project_account(account) for account in db(request).list_accounts()]}

    @app.patch("/api/v2/accounts/{account_id}/identity")
    def account_identity(request: Request, account_id: str, payload: AccountIdentityRequest):
        from .account_identity import correct_identity
        return correct_identity(db(request), account_id, **payload.model_dump())

    @app.put("/api/v2/accounts/{account_id}/verification-sources")
    def account_sources(request: Request, account_id: str, payload: VerificationSourcesRequest):
        from .account_identity import register_sources
        return register_sources(db(request), account_id, [source.model_dump() for source in payload.sources])

    @app.patch("/api/v2/accounts/{account_id}/relevance")
    def account_relevance(request: Request, account_id: str, payload: AccountRelevanceRequest):
        from .account_identity import correct_relevance
        return correct_relevance(db(request), account_id, **payload.model_dump())

    @app.get("/api/v2/accounts/network")
    def account_network(request: Request):
        return db(request).registry_graph()

    @app.post("/api/v2/accounts/check-public-sources", status_code=202)
    def account_checks(request: Request, payload: AccountCheckRequest):
        from .account_verification import verification_source
        store = db(request)
        selected = list(dict.fromkeys(payload.account_ids)) or [a["id"] for a in store.list_accounts() if a["status"] == "candidate"][:20]
        if not selected:
            raise ValueError("没有需要核验的候选账号。")
        if payload.source_url and len(selected) != 1:
            raise ValueError("指定核验依据时请选择一个账号。")
        for identity in selected:
            account = store.get_account(identity)
            if not account:
                raise LookupError("账号不存在。")
            verification_source(account, payload.source_url)
        with store._transaction(write=True) as connection:
            if store._postgres:
                store._execute(connection, "SELECT pg_advisory_xact_lock(1278032974)")
            # One active automatic check per registry prevents double clicks
            # across windows from creating repeated source requests.
            suffix = " FOR UPDATE" if store._postgres else ""
            rows = store._execute(connection, "SELECT * FROM wb_jobs WHERE kind='collect' AND status IN ('queued','running')" + suffix).fetchall()
            for row in rows:
                existing = store._job(row)
                if existing["params"].get("_account_verification"):
                    raise HTTPException(409, "已有公开账号核验任务正在执行，请在任务中心查看。")
            return store.create_job("collect", {"_account_verification": True, "source": "public_verification", "account_ids": selected, "verification_source_url": payload.source_url, "verification_force": payload.force}, _connection=connection)

    @app.get("/api/v2/accounts/{account_id}/relations")
    def account_relations(request: Request, account_id: str, page: int = Query(1, ge=1), page_size: int = Query(1000, ge=1, le=1000)):
        return db(request).account_relations(account_id, page=page, page_size=page_size)

    @app.post("/api/v2/accounts", status_code=201)
    def account_create(request: Request, payload: AccountData):
        from .account_identity import project_account
        account = db(request).save_account(payload.model_dump())
        return project_account(account)

    @app.patch("/api/v2/accounts/{account_id}")
    def account_edit(request: Request, account_id: str, payload: AccountData):
        from .account_identity import project_account
        account = db(request).save_account(payload.model_dump(exclude_unset=True), id=account_id)
        return project_account(account)

    @app.post("/api/v2/accounts/{account_id}/verify")
    def account_verify(request: Request, account_id: str, payload: VerifyRequest):
        from .account_identity import project_account
        account = db(request).save_account({"status": "verified" if payload.verified else "excluded", "evidence_url": payload.evidence_url}, id=account_id)
        return project_account(account)

    @app.get("/api/v2/jobs")
    def jobs(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
        from .report_inputs import project_report_job
        result = db(request).list_jobs(page=page, page_size=page_size)
        return {**result, "items": [project_report_job(job) for job in result["items"]]}

    @app.get("/api/v2/jobs/{job_id}")
    def job(request: Request, job_id: str):
        from .report_inputs import project_report_job
        result = db(request).get_job(job_id)
        if result is None:
            raise LookupError(job_id)
        return {**project_report_job(result), "events": db(request).job_events(job_id)}

    @app.post("/api/v2/jobs", status_code=202)
    def create_job(request: Request, payload: JobRequest):
        model = {"collect": CollectParams, "report": ReportParams, "login": LoginParams, "research": ResearchParams}[payload.kind]
        params = model.model_validate(payload.params).model_dump(mode="json")
        if payload.kind == "login" and params.get("method") == "firefox":
            from .social.firefox_import import resolve_firefox_profile
            resolve_firefox_profile(params["profile_id"])
        if payload.kind == "research":
            from .research_estimate import validate_confirmation
            validate_confirmation(params, payload.preview_token)
            if params.get("analysis_mode") == "ai" and not pipeline.capabilities()["llm"]["configured"]:
                raise ValueError("尚未配置 AI 连接，可选择自动或规则模式。")
            if params.get("resume_run_id"):
                raise ValueError("继续已有研究请使用任务重试入口。")
        if payload.kind == "report" and params.get("research_run_id"):
            from .research_reports import validate_report_run
            exported = db(request).export_research(params["research_run_id"])
            run = validate_report_run(exported)
            params["research_run_id"] = run["id"]
            params["read_original"] = False
            for field in ("start_date", "end_date"):
                if not params.get(field):
                    params[field] = (run.get("params") or {}).get(field, "")
            if params["title"] == "日本和平运动研究简报" and (run.get("params") or {}).get("research_goal"):
                params["title"] = (run["params"]["research_goal"][:145] + " · 研究分析")[:160]
        if params.get("mode") == "ai" and not pipeline.capabilities()["llm"]["configured"]:
            raise ValueError("尚未配置 AI 密钥。可以先生成资料简报，或在本机配置 AI 后重试。")
        if payload.kind == "report" and params.get("analysis_harness") == "dsh":
            from .harness_runtime import capability as harness_capability
            if not harness_capability()["available"]:
                raise ValueError("DSH 研究运行时未安装，请使用标准 AI 分析或安装完整应用版本。")
        created = db(request).create_job(payload.kind, params)
        # Worker also polls, so this threadpool handler needs no event-loop access.
        return created

    @app.post("/api/v2/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str):
        from .report_inputs import project_report_job
        result = db(request).request_cancel(job_id)
        if result is None:
            raise LookupError(job_id)
        if (result['status'] == 'cancelled' and result['kind'] == 'collect'
                and result['params'].get('_account_verification') is True):
            from .account_verification import finish_schedule
            finish_schedule(db(request), result, 'cancelled')
        return project_report_job(result)

    @app.get("/api/v2/jobs/{job_id}/retry-preview")
    def retry_preview(request: Request, job_id: str):
        return db(request).report_retry_preview(job_id)

    @app.post("/api/v2/jobs/{job_id}/retry", status_code=202)
    def retry_job(request: Request, job_id: str, options: ReportRetryOptions | None = None):
        from .report_inputs import project_report_job
        overrides = options.model_dump(exclude_none=True) if options else None
        def validate_effective_report(params):
            if params.get("analysis_harness") == "dsh":
                from .harness_runtime import capability as harness_capability
                if not harness_capability()["available"] or not pipeline.capabilities()["llm"]["configured"]:
                    raise ValueError("DSH 工具分析需要已安装研究运行时及就绪的 AI 连接。")
        try:
            return project_report_job(db(request).retry_job(job_id, report_options=overrides, report_validator=validate_effective_report))
        except RetryConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v2/reports")
    def reports(request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
        return db(request).list_reports(page=page, page_size=page_size)

    @app.get("/api/v2/reports/{report_id}")
    def report(request: Request, report_id: str):
        result = db(request).get_report(report_id)
        if result is None:
            raise LookupError(report_id)
        return result

    @app.get("/api/v2/reports/{report_id}/download")
    def report_download(request: Request, report_id: str):
        result = db(request).get_report(report_id)
        if result is None:
            raise LookupError(report_id)
        return Response(result["markdown"], media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="japan-peace-{result["id"]}.md"'})

    from .agent_api import register_agent_api
    register_agent_api(app)
    return app
