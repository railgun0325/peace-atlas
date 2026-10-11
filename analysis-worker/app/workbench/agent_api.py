"""Explicit, revocable local Agent access; never exposes the desktop credential.

Public discovery contains contracts only. Every data/action request requires its
own grant, an origin-free loopback client, and the operation's allowed scope.
Business requests pass through the existing authenticated API and validation.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


ASSETS = Path(__file__).with_name("agent_assets")
PREVIEW_TTL = 300
MAX_PREVIEWS = 256
MAX_RECEIPTS = 512
MAX_BODY = 2 * 1024 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationRequest(StrictModel):
    operation: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExecuteRequest(OperationRequest):
    preview_id: str = Field(min_length=1, max_length=100)
    operation_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,80}$")
    confirmed: Literal[True]


class GrantRequest(StrictModel):
    label: str = Field(default="本机外部 Agent", min_length=1, max_length=80)
    scopes: list[Literal["read", "run", "cleanup", "review"]] = Field(default_factory=lambda: ["read"])
    credential_path: str = Field(min_length=1, max_length=4096)
    ttl_seconds: int = Field(default=28800, ge=300, le=86400)


class EmptyArgs(StrictModel):
    pass


class PageArgs(StrictModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class ItemsArgs(PageArgs):
    query: str = Field(default="", max_length=1200)
    type: Literal["", "news", "social"] = ""
    topic: str = Field(default="", max_length=100)
    source: str = Field(default="", max_length=100)
    start_date: str = Field(default="", max_length=10)
    end_date: str = Field(default="", max_length=10)
    trash: bool = False


class RecordArgs(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class StartArgs(StrictModel):
    params: dict[str, Any]


class ContinueArgs(RecordArgs):
    limits: dict[str, Any] = Field(default_factory=dict)


class RetryArgs(RecordArgs):
    options: dict[str, Any] = Field(default_factory=dict)


class CleanupArgs(StrictModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["trash", "restore"]
    reason: str = Field(min_length=1, max_length=500)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error(code, message, status):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _loopback(request):
    try:
        return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        return False


def write_private_connection(path: Path, payload: dict):
    """Create only a new caller-selected file, restrict ACL before writing secrets."""
    if not path.is_absolute() or path.suffix.lower() != ".json" or not path.parent.is_dir():
        raise ValueError("请选择已有文件夹内的新 JSON 连接文件。")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            descriptor = ctypes.c_void_p()
            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
            convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
            convert.restype = wintypes.BOOL
            secure = advapi.SetFileSecurityW
            secure.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
            secure.restype = wintypes.BOOL
            if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, ctypes.byref(descriptor), None):
                raise OSError("无法建立仅文件所有者可读的连接文件。")
            try:
                if not secure(str(path), 0x00000004 | 0x80000000, descriptor):
                    raise OSError("无法设置连接文件的私有权限；授权未创建。")
            finally:
                ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(descriptor)
        else:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            fd = None
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if fd is not None:
            os.close(fd)
        # os.O_EXCL proves this exact file was created by this invocation.
        path.unlink(missing_ok=True)
        raise


class AgentAccess:
    def __init__(self, app, clock=time.time):
        self.app = app
        self.clock = clock
        self.grants = {}
        self.previews = {}
        self.receipts = {}
        self.lock = asyncio.Lock()

    def authorize(self, request):
        if not _loopback(request) or any(request.headers.get(x) for x in ("origin", "referer")):
            raise HTTPException(403, "Agent 连接只接受无浏览器来源的本机客户端。")
        supplied = request.headers.get("x-peace-atlas-agent", "")
        digest = hashlib.sha256(supplied.encode()).hexdigest()
        grant = self.grants.get(digest)
        if not supplied or grant is None or grant["expires_at"] <= self.clock():
            raise HTTPException(401, "请先在桌面菜单明确授权外部 Agent，或重新授权已过期的连接。")
        return grant

    def require_scope(self, grant, operation, arguments=None):
        if operation not in self.operations:
            raise HTTPException(404, "未开放该 Agent 操作。")
        spec = self.operations[operation]
        scope = spec["scope"]
        if operation in {"job.cancel", "job.retry"}:
            if not {"run", "review"}.intersection(grant["scopes"]):
                raise HTTPException(403, "本次授权不包含此操作的权限。")
            identity = RecordArgs.model_validate({"id": (arguments or {}).get("id")}).id
            job = self.app.state.store.get_job(identity)
            if job is None:
                raise HTTPException(404, "任务不存在。")
            if job["kind"] not in {"collect", "research", "report"}:
                raise HTTPException(403, "Agent 不能操作登录或会话任务。")
            if job["kind"] == "collect" and (job["params"].get("_evidence_review") is True or job["params"].get("_account_classification") is True):
                scope = "review"
        if scope not in grant["scopes"]:
            raise HTTPException(403, "本次授权不包含此操作的权限。")
        return {**spec, "scope": scope}

    async def invoke(self, method, path, *, body=None, query=None):
        # No arbitrary URL/path input is accepted. The desktop token stays only
        # inside this process and still passes the original API session gate.
        token = os.getenv("WORKBENCH_SESSION_TOKEN", "")
        if not token:
            return _error("desktop_unavailable", "桌面授权服务未就绪。", 503)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                     base_url="http://127.0.0.1",
                                     headers={"x-workbench-token": token}) as client:
            response = await client.request(method, path, json=body, params=query)
        try:
            value = response.json()
        except ValueError:
            return _error("unexpected_response", "业务接口没有返回可读取的结果。", 502)
        return JSONResponse(value, status_code=response.status_code)

    def normalize(self, operation, values):
        spec = self.operations[operation]
        args = spec["model"].model_validate(values).model_dump(mode="json")
        if "params_model" in spec:
            args["params"] = spec["params_model"].model_validate(args["params"]).model_dump(mode="json")
        if operation == "research.start" and args["params"].get("resume_run_id"):
            raise ValueError("已有研究请使用 research.continue。")
        if operation == "research.continue":
            from .server import ResearchContinuationParams
            parsed = ResearchContinuationParams.model_validate(args["limits"])
            if parsed.preview_token:
                raise ValueError("续查确认由 Agent 预览生成，不能自行提供业务令牌。")
            args["limits"] = parsed.model_dump(exclude_none=True, exclude={"preview_token"})
        if operation == "job.retry":
            from .server import ReportRetryOptions
            args["options"] = ReportRetryOptions.model_validate(args["options"]).model_dump(exclude_none=True)
        if operation == "query.plan":
            if args.get("use_ai"):
                raise ValueError("Agent 查询规划只提供不付费的规则预览。")
        if operation == "items.cleanup" and not args["reason"].strip():
            raise ValueError("请记录清理或恢复原因。")
        return args

    async def read(self, operation, args):
        spec = self.operations[operation]
        path = spec["path"].format(id=args.get("id", ""))
        if operation == "query.plan":
            return await self.invoke("POST", path, body=args)
        result = await self.invoke("GET", path, query={k: v for k, v in args.items() if k != "id"})
        if result.status_code < 400 and operation in {"job.get", "job.retry_preview", "jobs.list"}:
            value = json.loads(result.body)
            if operation in {"job.get", "job.retry_preview"} and value.get("kind") not in {"collect", "research", "report"}:
                raise HTTPException(403, "Agent 不能读取登录或会话任务。")
            if operation == "jobs.list":
                value["items"] = [x for x in value.get("items", []) if x.get("kind") in {"collect", "research", "report"}]
                value["agent_filtered"] = True
                result = JSONResponse(value)
        return result

    async def business_preview(self, operation, args):
        if operation == 'review.batch':
            return await self.invoke('POST', '/api/v2/reviews/batch/preview', body=args)
        if operation == 'review.start':
            return await self.invoke('POST', '/api/v2/reviews/estimate', body=args)
        if operation == 'classify.start':
            return await self.invoke('POST', '/api/v2/accounts/classify/estimate', body=args)
        if operation == "research.start":
            return await self.invoke("POST", "/api/v2/research/estimate", body=args["params"])
        if operation == "research.continue":
            return await self.invoke("POST", f"/api/v2/research/{args['id']}/continue-estimate", body=args["limits"])
        if operation == "items.cleanup":
            return await self.invoke("POST", "/api/v2/items/cleanup/preview", body=args)
        if operation in {"job.cancel", "job.retry"}:
            result = await self.invoke("GET", f"/api/v2/jobs/{args['id']}")
            if result.status_code >= 400:
                return result
            job = json.loads(result.body)
            if job["kind"] not in {"collect", "research", "report"}:
                raise HTTPException(403, "Agent 不能操作登录或会话任务。")
            if operation == "job.retry" and job["kind"] == "report":
                return await self.invoke("GET", f"/api/v2/jobs/{args['id']}/retry-preview")
            return JSONResponse({"job_id": job["id"], "kind": job["kind"], "status": job["status"],
                                 "saved_params": job["params"], "result": job.get("result"),
                                 "may_use_model": job["kind"] == "report" or job["params"].get("analysis_mode") != "rules"})
        return JSONResponse({"kind": self.operations[operation]["kind"], "params": args["params"],
                             "may_use_model": args["params"].get("mode") == "ai",
                             "may_read_public_network": bool(args["params"].get("read_original", operation == "collection.start")),
                             "note": "预览不排队；执行后资料与报告按既有业务规则保存。"})

    async def execute(self, operation, args, proof):
        if operation == 'review.batch':
            return await self.invoke('POST', '/api/v2/reviews/batch/apply', body={**args, 'preview_digest': proof['digest']})
        if operation == 'review.start':
            return await self.invoke('POST', '/api/v2/reviews/start', body={**args, 'input_digest': proof['input_digest']})
        if operation == 'classify.start':
            return await self.invoke('POST', '/api/v2/accounts/classify/start', body={**args, 'input_digest': proof['input_digest']})
        if operation in {"research.start", "report.start", "collection.start"}:
            body = {"kind": self.operations[operation]["kind"], "params": args["params"]}
            if operation == "research.start":
                body["preview_token"] = proof["preview_token"]
            return await self.invoke("POST", "/api/v2/jobs", body=body)
        if operation == "research.continue":
            return await self.invoke("POST", f"/api/v2/research/{args['id']}/continue",
                                     body={**args["limits"], "preview_token": proof["preview_token"]})
        if operation in {"job.cancel", "job.retry"}:
            action = operation.split(".")[1]
            options = args.get("options")
            if action == "retry" and proof.get("kind") == "report":
                options = dict(options or {})
                mode = options.get("input_mode", "original")
                options.setdefault("input_sha256", (proof.get("original_input" if mode == "original" else "current_input") or {}).get("sha256"))
            return await self.invoke("POST", f"/api/v2/jobs/{args['id']}/{action}",
                                     body=options if action == "retry" else None)
        if operation == "items.cleanup":
            return await self.invoke("POST", "/api/v2/items/cleanup/apply",
                                     body={**args, "preview_digest": proof["preview_digest"]})
        raise HTTPException(404, "未开放该 Agent 操作。")


def register_agent_api(app):
    """Call after the original routes are registered. No auth middleware bypass."""
    from .server import CollectParams, QueryPlanParams, ReportParams, ResearchParams
    from .review import QueueArgs, BatchArgs, AutoReviewArgs
    from .fast_account_classification import ClassificationArgs
    access = AgentAccess(app)
    app.state.agent_access = access
    access.operations = {
        "overview.get": {"scope": "read", "model": EmptyArgs, "path": "/api/v2/overview"},
        "items.list": {"scope": "read", "model": ItemsArgs, "path": "/api/v2/items"},
        "items.get": {"scope": "read", "model": RecordArgs, "path": "/api/v2/items/{id}"},
        "accounts.list": {"scope": "read", "model": EmptyArgs, "path": "/api/v2/accounts"},
        "jobs.list": {"scope": "read", "model": PageArgs, "path": "/api/v2/jobs"},
        "job.get": {"scope": "read", "model": RecordArgs, "path": "/api/v2/jobs/{id}"},
        "job.retry_preview": {"scope": "read", "model": RecordArgs, "path": "/api/v2/jobs/{id}/retry-preview"},
        "reports.list": {"scope": "read", "model": PageArgs, "path": "/api/v2/reports"},
        "report.get": {"scope": "read", "model": RecordArgs, "path": "/api/v2/reports/{id}"},
        "research.list": {"scope": "read", "model": PageArgs, "path": "/api/v2/research"},
        "research.graph": {"scope": "read", "model": RecordArgs, "path": "/api/v2/research/{id}"},
        "research.export": {"scope": "read", "model": RecordArgs, "path": "/api/v2/research/{id}/export"},
        "query.plan": {"scope": "read", "model": QueryPlanParams, "path": "/api/v2/research/plan"},
        "research.start": {"scope": "run", "model": StartArgs, "params_model": ResearchParams, "kind": "research"},
        "report.start": {"scope": "run", "model": StartArgs, "params_model": ReportParams, "kind": "report"},
        "collection.start": {"scope": "run", "model": StartArgs, "params_model": CollectParams, "kind": "collect"},
        "research.continue": {"scope": "run", "model": ContinueArgs},
        "job.cancel": {"scope": "run", "model": RecordArgs},
        "job.retry": {"scope": "run", "model": RetryArgs},
        "items.cleanup": {"scope": "cleanup", "model": CleanupArgs},
        'review.queue': {'scope': 'read', 'model': QueueArgs, 'path': '/api/v2/reviews/queue'},
        'review.batch': {'scope': 'review', 'model': BatchArgs},
        'review.start': {'scope': 'review', 'model': AutoReviewArgs},
        'classify.start': {'scope': 'review', 'model': ClassificationArgs},
    }
    router = APIRouter()

    @app.middleware("http")
    async def agent_boundary(request, call_next):
        if request.url.path.startswith("/agents/"):
            if not _loopback(request) or request.headers.get("origin") or request.headers.get("referer"):
                return _error("agent_local_client_required", "Agent 接口只供本机命令客户端使用。", 403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or len(length) > 8 or int(length) > MAX_BODY:
                return _error("agent_body_limit", "Agent 请求超出允许大小。", 413)
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY:
                    return _error("agent_body_limit", "Agent 请求超出允许大小。", 413)
                chunks.append(chunk)
            # Starlette forwards this bounded body cache downstream. Reject
            # chunked input immediately at the bound instead of draining it.
            request._body = b"".join(chunks)
        response = await call_next(request)
        if request.url.path.startswith("/agents/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def desktop_admin(request):
        expected = os.getenv("WORKBENCH_SESSION_TOKEN", "")
        # Native desktop control sends this header. Browser cookies cannot
        # create grants or select a credential-export path on their own.
        supplied = request.headers.get("x-workbench-token", "")
        if (not _loopback(request) or request.headers.get("origin") or request.headers.get("referer")
                or not expected or not hmac.compare_digest(expected, supplied)):
            raise HTTPException(401, "请从已打开的桌面应用管理 Agent 授权。")

    @router.get("/api/v2/agent/access")
    def list_access(request: Request):
        desktop_admin(request)
        return {"items": [{k: v for k, v in grant.items() if k != "token_hash"}
                          for grant in access.grants.values() if grant["expires_at"] > access.clock()]}

    @router.post("/api/v2/agent/access", status_code=201)
    def grant_access(request: Request, payload: GrantRequest):
        desktop_admin(request)
        if len(access.grants) >= 16:
            raise HTTPException(409, "授权数量已达上限，请先撤销旧授权。")
        scopes = list(dict.fromkeys(["read", *payload.scopes]))
        secret = secrets.token_urlsafe(32)
        identifier = secrets.token_hex(12)
        expires = access.clock() + payload.ttl_seconds
        base = str(request.base_url).rstrip("/")
        from urllib.parse import urlsplit
        if urlsplit(base).hostname not in {"127.0.0.1", "localhost"}:
            raise HTTPException(403, "授权端点必须使用本机回环地址。")
        path = Path(payload.credential_path)
        write_private_connection(path, {"protocol": 1, "base_url": base, "agent_token": secret,
                                        "grant_id": identifier, "expires_at": expires, "scopes": scopes})
        digest = hashlib.sha256(secret.encode()).hexdigest()
        grant = {"id": identifier, "label": payload.label, "scopes": scopes,
                 "expires_at": expires, "credential_path": str(path)}
        access.grants[digest] = grant
        return {**grant, "token_returned": False, "restart_revokes_access": True}

    @router.delete("/api/v2/agent/access")
    def revoke_access(request: Request):
        desktop_admin(request)
        count = len(access.grants)
        access.grants.clear()
        access.previews.clear()
        access.receipts.clear()
        return {"revoked": count, "credential_files_retained_but_invalid": True}

    @router.get("/agents/v1/manifest")
    def manifest():
        return {"name": "PEACE ATLAS", "protocol": 1, "default_access": "disabled",
                "authorization": "desktop explicit grant; independent owner-only connection file",
                "schema": "/agents/v1/schema", "skill": "/agents/v1/skill",
                "transport": "loopback HTTP via trusted local CLI; not browser CORS",
                "install_hint": "桌面菜单 → 外部 Agent → 导出授权连接，再安装随包 peace-atlas Skill。",
                "forbidden": ["AI configuration", "keys", "login", "profiles", "purge", "shutdown"],
                "restart_revokes_access": True}

    @router.get("/agents/v1/skill")
    def skill():
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse((ASSETS / "peace-atlas/SKILL.md").read_text(encoding="utf-8"))

    @router.get("/agents/v1/schema")
    def schema():
        operations = {}
        for name, spec in access.operations.items():
            shape = spec["model"].model_json_schema()
            if "params_model" in spec:
                nested = spec["params_model"].model_json_schema()
                shape.setdefault("$defs", {}).update(nested.pop("$defs", {}))
                shape["properties"]["params"] = nested
            if name in {"research.continue", "job.retry"}:
                from .server import ResearchContinuationParams, ReportRetryOptions
                model = ResearchContinuationParams if name == "research.continue" else ReportRetryOptions
                nested = model.model_json_schema()
                nested["properties"].pop("preview_token", None)
                shape.setdefault("$defs", {}).update(nested.pop("$defs", {}))
                shape["properties"]["limits" if name == "research.continue" else "options"] = nested
            if name == "query.plan":
                shape["properties"]["use_ai"] = {"type": "boolean", "const": False, "default": False}
            operations[name] = {"scope": spec["scope"], "mutates": spec["scope"] != "read",
                                "arguments": shape}
            if name == 'classify.start':
                operations[name].update(description='快速批量组织/个人粗分，可人工更改；仅名称、用户名和已保存公开简介，未见明确组织发布线索时归个人。',
                    classification_types=['organization', 'individual'],
                    may_read_public_network=False, identity_verification=False, manual_locked_skipped=True)
            if name in {"job.cancel", "job.retry"}:
                operations[name]["scope_by_job"] = {"evidence_review": "review", "account_classification": "review", "other_business": "run"}
        return {"protocol": 1, "operations": operations,
                "read_endpoint": "/agents/v1/read", "preview_endpoint": "/agents/v1/preview",
                "execute_endpoint": "/agents/v1/execute", "preview_ttl_seconds": PREVIEW_TTL,
                "idempotency": "per grant/backend lifetime; same key and arguments returns original result, including failure",
                "execute_schema": ExecuteRequest.model_json_schema()}

    @router.post("/agents/v1/read")
    async def read_operation(request: Request, payload: OperationRequest):
        grant = access.authorize(request)
        spec = access.require_scope(grant, payload.operation, payload.arguments)
        if spec["scope"] != "read":
            raise HTTPException(409, "该操作需要预览与确认。")
        args = access.normalize(payload.operation, payload.arguments)
        return await access.read(payload.operation, args)

    @router.post("/agents/v1/preview")
    async def preview_operation(request: Request, payload: OperationRequest):
        grant = access.authorize(request)
        spec = access.require_scope(grant, payload.operation, payload.arguments)
        if spec["scope"] == "read":
            raise HTTPException(422, "查询操作请使用 read。")
        args = access.normalize(payload.operation, payload.arguments)
        response = await access.business_preview(payload.operation, args)
        if response.status_code >= 400:
            return response
        access.previews = {k: v for k, v in access.previews.items() if v["expires_at"] > access.clock()}
        if len(access.previews) >= MAX_PREVIEWS:
            raise HTTPException(409, "待确认预览已达上限，请等待旧预览到期。")
        ticket = secrets.token_urlsafe(24)
        proof = json.loads(response.body)
        access.previews[ticket] = {"grant_id": grant["id"], "operation": payload.operation,
                                   "arguments": args, "proof": proof,
                                   "expires_at": access.clock() + PREVIEW_TTL, "executed_key": None}
        return {"preview_id": ticket, "operation": payload.operation, "arguments": args,
                "preview": proof, "expires_at": access.previews[ticket]["expires_at"],
                "confirmation_required": True, "created_jobs": 0}

    @router.post("/agents/v1/execute")
    async def execute_operation(request: Request, payload: ExecuteRequest):
        grant = access.authorize(request)
        spec = access.require_scope(grant, payload.operation, payload.arguments)
        if spec["scope"] == "read":
            raise HTTPException(422, "查询操作请使用 read。")
        args = access.normalize(payload.operation, payload.arguments)
        identity = (grant["id"], payload.operation_key)
        fingerprint = _canonical({"operation": payload.operation, "arguments": args})
        async with access.lock:
            access.authorize(request)
            existing = access.receipts.get(identity)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise HTTPException(409, "同一操作键不能改用另一操作或参数。")
                return JSONResponse(existing["result"], status_code=existing["status"],
                                    headers={"X-Peace-Atlas-Replayed": "true"})
            ticket = access.previews.get(payload.preview_id)
            if (not ticket or ticket["grant_id"] != grant["id"] or ticket["operation"] != payload.operation
                    or _canonical(ticket["arguments"]) != _canonical(args) or ticket["expires_at"] <= access.clock()):
                raise HTTPException(409, "预览已过期、参数已改变或不属于当前授权，请重新预览。")
            if ticket["executed_key"] is not None:
                raise HTTPException(409, "该预览已经执行；重试网络请求请保留原操作键。")
            if len(access.receipts) >= MAX_RECEIPTS:
                raise HTTPException(409, "本次授权会话操作记录已满，请在桌面撤销并重新授权。")
            ticket["executed_key"] = payload.operation_key
            uncertain = _error("agent_execution_uncertain", "操作结果未确认；请先查询任务状态，勿换操作键重复执行。", 500)
            # Reserve the uncertain receipt before the first mutation await.
            # Cancellation (a BaseException) propagates normally, but cannot
            # leave a consumed preview with no result for the original key.
            access.receipts[identity] = {"fingerprint": fingerprint, "status": 500,
                                         "result": json.loads(uncertain.body)}
            try:
                response = await access.execute(payload.operation, args, ticket["proof"])
            except Exception:
                # Do not retry uncertain mutations, discard a receipt, or log
                # exception values that can contain local credentials/paths.
                response = uncertain
            result = json.loads(response.body)
            access.receipts[identity] = {"fingerprint": fingerprint, "status": response.status_code, "result": result}
            return response

    app.include_router(router)
    return access
