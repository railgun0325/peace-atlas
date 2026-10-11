"""Durable serial work and login lanes. Browser connections never own the work."""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import datetime, timezone

from . import pipeline
from .store import Store, TERMINAL
from .limits import RESEARCH_TIME_DEFAULT
from .report_progress import report_note_progress, report_note_progress_message

log = logging.getLogger(__name__)


class CancelledByUser(asyncio.CancelledError):
    """A user stop follows cancellation cleanup and bypasses error recovery."""
    pass


class SourceCollectionError(ValueError):
    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.result = {"fetched": 0, "inserted": 0, "diagnostics": diagnostics}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRunner:
    def __init__(self, store: Store):
        self.store = store
        self.wake = asyncio.Event()
        self.stopping = False
        self.task: asyncio.Task | None = None
        self.login_task: asyncio.Task | None = None
        self.verification_task: asyncio.Task | None = None
        self.research_contexts = {}
        self.verification_contexts = {}

    async def start(self):
        if any(task and not task.done() for task in (self.task, self.login_task, self.verification_task)):
            return
        self.stopping = False
        def recover():
            with self.store._transaction() as connection:
                previous = [self.store._job(row) for row in self.store._execute(connection,
                    "SELECT * FROM wb_jobs WHERE kind='collect' AND status='running'").fetchall()]
            count = self.store.recover_jobs()
            from .account_verification import finish_schedule
            for job in previous:
                if job['params'].get('_account_verification') is True:
                    finish_schedule(self.store, job, 'interrupted')
            return count
        recovered = await asyncio.to_thread(recover)
        if recovered:
            log.info("Marked %s interrupted tasks for explicit retry", recovered)
        self.task = asyncio.create_task(self._loop(exclude_kind="login", verification=False), name="workbench-worker")
        # A visible login may wait ten minutes for the user. It has one bounded
        # lane of its own, so it cannot occupy the collection/report worker.
        self.login_task = asyncio.create_task(self._loop(kind="login"), name="workbench-login-worker")
        self.verification_task = asyncio.create_task(self._loop(kind="collect", verification=True), name="workbench-verification-worker")

    async def stop(self):
        tasks = [task for task in (self.task, self.login_task, self.verification_task) if task]
        if not self.stopping:
            self.stopping = True
            self.wake.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
        # Cancel both before joining either: a saving normal job must not delay
        # closing the login window. Repeated stop calls do not cancel cleanup.
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                raise outcome

    async def _loop(self, **claim_filter):
        while not self.stopping:
            try:
                # Cancelling to_thread does not stop its database transaction.
                # Join a pending claim before exiting, then terminalize anything
                # it acquired so no orphaned running record is left behind.
                claiming = asyncio.create_task(asyncio.to_thread(self.store.claim_job, **claim_filter))
                try:
                    job = await asyncio.shield(claiming)
                except asyncio.CancelledError:
                    job = await claiming
                    if job:
                        await self._terminal(job["id"], "interrupted", "应用退出，尚未执行的已领取任务已标记为中断。", "warning",
                                       error="应用退出，任务中断；可在任务中心重试。")
                    raise
                if job:
                    await self._execute(job)
                    continue
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Worker loop failed; queue retained")
                await asyncio.sleep(1)

    async def _execute(self, job):
        jid = job["id"]
        committing = asyncio.Event()
        work = None
        try:
            await asyncio.to_thread(self.store.add_job_event, jid, "任务开始执行；进度已保存，可关闭或刷新页面。")
            work = asyncio.create_task(self._perform(job, committing))
            while not work.done():
                await asyncio.wait({work}, timeout=0.25)
                if work.done():
                    break
                latest = await asyncio.to_thread(self.store.get_job, jid)
                if latest and latest.get("cancel_requested") and not work.done() and not committing.is_set():
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    await self._terminal(jid, "cancelled", "任务已取消。已完成的保存操作会保留。", "warning")
                    return
            result = await work
        except CancelledByUser:
            await self._terminal(jid, "cancelled", "已在保存前取消。", "warning")
        except asyncio.CancelledError:
            if not self.stopping and not asyncio.current_task().cancelling():
                # A child/dependency can cancel itself without the app exiting.
                # Preserve the queue lane and record a real failed task; do not
                # mislabel it as shutdown or silently stop future work.
                await self._failed(jid, pipeline.PipelineError("任务内部读取或分析被意外取消；应用仍在运行，已保存资料保留，可在任务中心重试。"))
                return
            if work:
                if committing.is_set():
                    # A database commit must reach a deterministic end, even on exit.
                    try:
                        result = await asyncio.shield(work)
                    except Exception as commit_error:
                        await self._failed(jid, commit_error)
                    else:
                        await self._completed(jid, result)
                    raise
                else:
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
            await self._terminal(jid, "interrupted", "应用退出，任务已安全标记为中断。", "warning",
                           error="应用退出，任务中断；可在任务中心重试。")
            raise
        except Exception as exc:
            # A monitoring/storage error must not abandon a still-running
            # collector or AI call while the loop starts the next job.
            if work:
                if committing.is_set():
                    try:
                        result = await asyncio.shield(work)
                    except Exception as commit_error:
                        exc = commit_error
                    else:
                        await self._completed(jid, result, monitoring_recovered=True)
                        return
                elif not work.done():
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
            await self._failed(jid, exc)
        else:
            # Finalization is outside the work try/except: a log failure must
            # never reclassify an already committed successful job as failed.
            await self._completed(jid, result)

    async def _terminal(self, jid, status, message, level="info", **fields):
        # Hold the produced result and the lane until its terminal state is
        # durable. A short storage failure must not abandon a saved report or
        # let another job start while the previous one still appears running.
        async def finalize():
            delay = 0.1
            while True:
                try:
                    await asyncio.to_thread(self._write_terminal, jid, status, message, level, **fields)
                    return
                except Exception:
                    log.exception("Job %s final state could not be saved; retaining its result and retrying", jid)
                    await asyncio.sleep(delay)
                    delay = min(1, delay * 2)

        finalizing = asyncio.create_task(finalize())
        try:
            await asyncio.shield(finalizing)
        except asyncio.CancelledError:
            # Normal shutdown joins finalization instead of replacing an
            # already saved success with an interruption and repeat request.
            await finalizing

    def _write_terminal(self, jid, status, message, level="info", **fields):
        context = self.research_contexts.get(jid)
        if context:
            run_id = context["run_id"]
            fields["result"] = {**context.get("result", {}), **fields.get("result", {}), "run_id": run_id}
            run = self.store.get_research_run(run_id)
            if run:
                self.store.checkpoint_research(run_id, status=status, phase="finished" if status in {"succeeded", "partial"} else status)
                fields["result"]["summary"] = run.get("summary", {})
        try:
            self.store.update_job(jid, status=status, finished_at=now(), **fields)
        except ValueError:
            # A write can commit before its caller observes an I/O failure.
            # Read the durable receipt directly: the monitoring getter itself
            # may be the failing component that brought us to finalization.
            with self.store._transaction() as connection:
                current = self._terminal_receipt(connection, jid)
            if not current or current["status"] not in TERMINAL:
                raise
            self.research_contexts.pop(jid, None)
            self._finish_verification(jid, current["status"])
            return
        self.research_contexts.pop(jid, None)
        self._finish_verification(jid, status)
        try:
            self.store.add_job_event(jid, message, level)
        except Exception:
            log.exception("Job %s reached %s but its final log event could not be saved", jid, status)

    def _finish_verification(self, jid, status):
        job = self.verification_contexts.get(jid)
        if job:
            from .account_verification import finish_schedule
            finish_schedule(self.store, job, status)
            self.verification_contexts.pop(jid, None)

    def _terminal_receipt(self, connection, jid):
        return self.store._execute(connection, "SELECT status FROM wb_jobs WHERE id=?", (jid,)).fetchone()

    async def _completed(self, jid, result, *, monitoring_recovered=False):
        result = dict(result)
        status = result.pop("_status", "succeeded")
        message = "任务完成。" if status == "succeeded" else "部分完成，请查看来源诊断。"
        if monitoring_recovered:
            message += " 保存已完成；执行期间的监控错误已恢复。"
        await self._terminal(jid, status, message, "info" if status == "succeeded" and not monitoring_recovered else "warning",
                       progress=100, result=result)

    async def _failed(self, jid, exc):
        # Infrastructure exceptions go only to local logs, never leak keys or
        # connection URLs through the job API.
        message = str(exc) if isinstance(exc, ValueError) else "处理失败，未标记为成功。请查看本机日志或重试。"
        log.error("Job %s failed", jid, exc_info=(type(exc), exc, exc.__traceback__))
        extra = {"result": exc.result} if isinstance(exc, SourceCollectionError) else {}
        await self._terminal(jid, "failed", message[:1200], "error", error=message[:1200], **extra)

    async def _perform(self, job, committing):
        jid, params = job["id"], job["params"]
        if job["kind"] == "login":
            from . import social
            self.store.update_job(jid, progress=15)
            if params.get("method", "auto") == "auto":
                from .social.auto_session import resolve_session
                self.store.add_job_event(jid, "正在验证当前工作台会话；会话缺失或失效时检查默认 Firefox 配置，不发起新的登录。")

                def before_auto_save():
                    if self.store.get_job(jid).get("cancel_requested"):
                        raise CancelledByUser()
                    committing.set()

                return await asyncio.wait_for(resolve_session(params["platform"], before_save=before_auto_save), timeout=110)
            if params.get("method") == "firefox":
                from .social.firefox_import import import_registered_firefox_x_session
                self.store.add_job_event(jid, "正在读取所选 Firefox 配置的 X 必要会话，并访问 X 首页验证；不会发起新的登录。")

                def before_save():
                    if self.store.get_job(jid).get("cancel_requested"):
                        raise CancelledByUser()
                    committing.set()

                result = await asyncio.wait_for(import_registered_firefox_x_session(params["profile_id"], before_save=before_save), timeout=60)
                return {**result, "method": "firefox", "state": "ready", "message": "已验证并保存 Firefox 中的 X 会话；原工作台会话已按需备份。"}
            self.store.add_job_event(jid, "已打开平台登录窗口。请在该窗口手动登录；最多等待 10 分钟，可随时取消。")
            result = await asyncio.wait_for(social.login(params["platform"]), timeout=620)
            if not result.get("ok"):
                raise ValueError(result.get("message") or "平台登录未完成，请重试。")
            return result
        if job["kind"] == "collect":
            if params.get('_account_classification') is True:
                from .fast_account_classification import run_classification
                previous_phase = None

                async def classification_checkpoint(value):
                    nonlocal previous_phase
                    await asyncio.to_thread(self.store.update_job, jid, result=value,
                        progress=round(value.get('completed', 0) / max(1, value.get('total', 0)) * 90))
                    if value.get('phase') != previous_phase:
                        previous_phase = value.get('phase')
                        labels = {'starting': '准备范围', 'classifying': 'DS 快速判定账号类型', 'saving': '保存本批类型', 'complete': '类型粗分完成'}
                        await asyncio.to_thread(self.store.add_job_event, jid,
                            f"类型粗分：{labels.get(previous_phase, '处理本批')}；已保存 {value.get('completed', 0)}/{value.get('total', 0)} 项。")

                result = await run_classification(self.store, job, checkpoint=classification_checkpoint,
                    cancelled=lambda: bool((self.store.get_job(jid) or {}).get('cancel_requested')))
                committing.set()
                return result
            if params.get('_evidence_review') is True:
                from .automated_review import run_auto_review
                previous_phase = None

                async def review_checkpoint(value):
                    nonlocal previous_phase
                    await asyncio.to_thread(self.store.update_job, jid, result=value,
                        progress=round(value.get('completed', 0) / max(1, value.get('total', 0)) * 90))
                    if value.get('phase') != previous_phase:
                        previous_phase = value.get('phase')
                        labels = {'starting': '准备范围', 'investigating': '阅读与调查公开依据', 'saving': '保存审核决定',
                                  'verifying_accounts': '核对准确账号回链', 'complete': '审核流程完成'}
                        await asyncio.to_thread(self.store.add_job_event, jid,
                            f"自动审核：{labels.get(previous_phase, '处理本批')}；已保存 {value.get('completed', 0)}/{value.get('total', 0)} 项。")

                result = await run_auto_review(self.store, job, checkpoint=review_checkpoint,
                    cancelled=lambda: bool((self.store.get_job(jid) or {}).get('cancel_requested')))
                committing.set()
                return result
            if params.get("_news_capture") is True:
                from .news_reader import capture_article
                selected = self.store.get_item(params["item_id"])
                if not selected or (selected.get("metadata") or {}).get("atlas_cleanup", {}).get("status") == "trashed":
                    raise ValueError("新闻已移除或进入回收站，未执行原文读取。")
                self.store.add_job_event(jid, "正在读取出版方公开原文并核对标题，原摘要保留为历史版本。")
                captured, diagnostic = await capture_article(selected, params.get("original_url", ""))
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
                if not diagnostic["ok"]:
                    return {"inserted": 0, "updated": 0, "fetched": 0, "diagnostics": [diagnostic], "_status": "partial"}
                committing.set()
                saved = await asyncio.to_thread(self.store.import_captured_news, selected, captured)
                if saved is None:
                    diagnostic.update(ok=False, outcome="source_changed", message="读取期间源资料发生更正，未用旧页面覆盖新资料；请重新读取。")
                    return {"inserted": 0, "updated": 0, "fetched": 0, "diagnostics": [diagnostic], "_status": "partial"}
                return {**saved, "fetched": 1, "diagnostics": [diagnostic]}
            if params.get("_account_verification") is True:
                from .account_verification import run_checks, trusted_sites, begin_schedule
                self.verification_contexts[jid] = job
                self.store.add_job_event(jid, "核对独立公开来源中的准确账号链接；无依据的账号保留候选状态。")
                await asyncio.to_thread(begin_schedule, self.store, job)
                sites = trusted_sites(await asyncio.to_thread(self.store.list_accounts))
                accounts = [self.store.get_account(identity) for identity in params["account_ids"]]
                results, save_lock = [], asyncio.Lock()

                async def save_check(check):
                    async with save_lock:
                        if self.store.get_job(jid).get("cancel_requested"):
                            raise CancelledByUser()

                        def checkpoint():
                            applied = self.store.apply_account_checks([check])
                            results.extend(applied["diagnostics"])
                            snapshot = {"checked": len(results), "verified": sum(row["verified"] for row in results),
                                "pending": sum(not row["verified"] and row["outcome"] != "retained" for row in results),
                                "diagnostics": list(results)}
                            self.store.update_job(jid, progress=round(len(results) / max(1, len(accounts)) * 90), result=snapshot)
                        saving = asyncio.create_task(asyncio.to_thread(checkpoint))
                        try:
                            await asyncio.shield(saving)
                        except asyncio.CancelledError:
                            await saving
                            raise

                await asyncio.wait_for(run_checks([a for a in accounts if a], params.get("verification_source_url", ""),
                    sites=sites, force=params.get("verification_force") is True,
                    retry=isinstance(params.get("_workbench_retry"), dict), on_result=save_check), 180)
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
                committing.set()
                result = {"checked": len(results), "verified": sum(row["verified"] for row in results),
                    "pending": sum(not row["verified"] and row["outcome"] != "retained" for row in results), "diagnostics": results}
                self.store.add_job_event(jid, f"完成 {result['checked']} 个公开来源检查，核验 {result['verified']} 个，仍待核验 {result['pending']} 个。")
                return {**result, "_status": "partial" if any(not row["ok"] for row in result["diagnostics"]) else "succeeded"}
            self.store.update_job(jid, progress=10)
            self.store.add_job_event(jid, "正在读取公开来源，按日本时间筛选日期。")
            is_social = params.get("source") in {"twitter", "instagram"}
            if is_social:
                from . import social, relevance

                async def progress(event):
                    message = event.get("message") or event.get("stage")
                    if message:
                        await asyncio.to_thread(self.store.add_job_event, jid, str(message)[:500])

                # Relevance is checked before an unrelated author can exhaust
                # the account budget. The adapter remains domain-independent.
                from .query_plan import build_query_plan
                plan = build_query_plan(params) if params.get("mode") != "account" and params.get("keyword", "").strip() else None
                collect = social.collect
                if plan and len(plan["groups"]) > 1:
                    from .social.collector import collect_queries
                    collect = collect_queries
                collected = await asyncio.wait_for(collect(params, progress=progress,
                    accept=lambda item: relevance.assess(item, params.get("relevance", "focused"))), timeout=360)
            else:
                from .query_plan import build_query_plan
                plan = build_query_plan(params) if params.get("keyword", "").strip() else None
                if plan and len(plan["groups"]) > 1:
                    from .query_execution import collect_queries
                    collected = await asyncio.wait_for(collect_queries(params), timeout=180)
                else:
                    collected = await asyncio.wait_for(pipeline.collect(params), timeout=180)
            diagnostics = collected.get("diagnostics", [])
            failures = [d for d in diagnostics if not d.get("ok", False)]
            if diagnostics and len(failures) == len(diagnostics) and not collected["items"]:
                raise SourceCollectionError("所选来源采集失败：" + "; ".join(str(d.get("message") or "来源不可访问") for d in failures)[:800], diagnostics)
            if is_social:
                selected, relevance_diagnostic = relevance.filter_items(collected["items"], params.get("relevance", "focused"), params.get("max_records", 30))
                diagnostics.append(relevance_diagnostic)
                normalized = []
                for raw in selected:
                    try:
                        normalized.append(pipeline.enrich_item(raw))
                    except ValueError as exc:
                        failure = {"source": params["source"], "ok": False, "outcome": "invalid_item", "message": str(exc)}
                        diagnostics.append(failure)
                        failures.append(failure)
                collected["items"] = normalized
            self.store.update_job(jid, progress=75)
            self.store.add_job_event(jid, f"读取 {len(collected['items'])} 条，开始去重和保存。")
            if self.store.get_job(jid).get("cancel_requested"):
                raise CancelledByUser()
            committing.set()
            if is_social:
                result = await asyncio.to_thread(self.store.import_social, collected["items"], collected.get("accounts", []), collected.get("relations", []))
            else:
                result = await asyncio.to_thread(self.store.import_items, collected["items"])
            if result.get("rejected") and not result.get("inserted") and not result.get("duplicates"):
                raise ValueError("来源内容全部未通过验证，未保存任何记录。")
            return {**result, "fetched": len(collected["items"]), "diagnostics": diagnostics,
                    "_status": "partial" if failures or result.get("rejected") or result.get("associations_rejected") else "succeeded"}
        if job["kind"] == "research":
            return await self._research(job)
        if job["kind"] == "report":
            self.store.update_job(jid, progress=10)
            from .report_inputs import resolve_report_input
            frozen_input = await asyncio.to_thread(resolve_report_input, self.store, job)
            input_state = {"value": frozen_input}
            research_context = None
            if frozen_input is not None:
                material = []
                research_context = frozen_input["snapshot"].get("research_context")
                self.store.add_job_event(jid, "沿已校验的完整报告输入快照继续；来源与审核上下文固定，后续阶段仍须通过各自指纹和证据校验。")
            elif params.get("research_run_id"):
                from .research_reports import prepare_research_report
                exported = await asyncio.to_thread(self.store.export_research, params["research_run_id"])
                material, research_context = prepare_research_report(exported)
                self.store.add_job_event(jid, "使用所选研究运行保存的资料版本与目标；账号候选及可见关注不会自动成为核验事实。")
            else:
                material = await asyncio.to_thread(self.store.report_items, params["start_date"], params["end_date"],
                    params.get("include_news", True), params.get("include_social", True))
            source_count = len(frozen_input["snapshot"]["items"]) if frozen_input else len(material)
            self.store.add_job_event(jid, f"选中 {source_count} 条资料，正在构建可追溯的来源快照。")
            self.store.update_job(jid, progress=35)
            capture_diagnostics = []
            if params.get("read_original", False) and not research_context and frozen_input is None:
                from .news_reader import capture_sources
                self.store.add_job_event(jid, "尝试读取可直接访问的出版方公开正文；聚合器地址和失败摘要分别标明。")
                enriched, capture_diagnostics = await capture_sources(material)
                changed = [(old, row) for old, row in zip(material, enriched) if row["content"] != old["content"]]
                if changed:
                    # Join the bounded write even when cancellation arrives.
                    def commit_sources():
                        for old, row in changed:
                            if self.store.import_captured_news(old, row) is None:
                                capture_diagnostics.append({"source": "public_article", "ok": False, "outcome": "source_changed", "item_id": old["id"], "message": "读取期间源资料发生变化，未覆盖新版本。"})
                    saving = asyncio.create_task(asyncio.to_thread(commit_sources))
                    try:
                        await asyncio.shield(saving)
                    except asyncio.CancelledError:
                        await saving
                        raise
                    material = await asyncio.to_thread(self.store.report_items, params["start_date"], params["end_date"],
                                                       params.get("include_news", True), params.get("include_social", True))
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
            parent_id = (params.get("_workbench_retry") or {}).get("parent_job_id")
            parent = self.store.get_job(parent_id) if parent_id else None
            from .job_retries import report_resume_checkpoint
            previous_review = report_resume_checkpoint(params, parent, self.store.get_job,
                frozen_input=frozen_input)
            async def checkpoint_input(value):
                input_state["value"] = copy.deepcopy(value)
                prior = self.store.get_job(jid).get("result") or {}
                saving = asyncio.create_task(asyncio.to_thread(self.store.update_job, jid,
                    result={**prior, "report_input_snapshot": value, "research_run_id": params.get("research_run_id", "")}))
                try:
                    await asyncio.shield(saving)
                except asyncio.CancelledError:
                    await saving
                    raise
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
            async def checkpoint_review(state):
                counts = state["coverage_progress"]
                result = {"report_checkpoint": state, "research_run_id": params.get("research_run_id", "")}
                if input_state["value"] is not None:
                    result["report_input_snapshot"] = copy.deepcopy(input_state["value"])
                note_detail = report_note_progress(state)
                synthesis_progress = note_detail["progress"] if note_detail else {}
                generation = state.get("report_generation") or {}
                stage_progress = (min(78, 55 + int(23 * synthesis_progress.get("completed_unit_count", 0) /
                    max(1, synthesis_progress.get("total_unit_count", 0)))) if synthesis_progress else None)
                if generation:
                    stage_progress = 92 if generation.get("complete") else 88 if generation.get("phase") == "review_pending" else 85
                saving = asyncio.create_task(asyncio.to_thread(self.store.update_job, jid, result=result,
                    progress=stage_progress if stage_progress is not None else min(55, 35 + int(20 * counts["reviewed_source_count"] / max(1, counts["input_source_count"])))))
                try:
                    await asyncio.shield(saving)
                except asyncio.CancelledError:
                    await saving
                    raise
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
                harness = state.get("harness_state") or {}
                message = ("报告草稿与来源复核已保存；正在完成报告。" if generation.get("complete") else
                    "报告草稿已保存，正在核对来源与研究目标。" if generation.get("phase") == "review_pending" else
                    "报告草稿发现须修问题，正在复用阅读结果修正综合分析。" if generation.get("phase") == "correction_required" else
                    f"正在合并第 {synthesis_progress.get('level', 0)} 层证据笔记；当前批次 {synthesis_progress.get('current_batch_number', 0)}/{synthesis_progress.get('planned_batch_count', 0)}，已完成 {synthesis_progress.get('completed_unit_count', 0)}/{synthesis_progress.get('total_unit_count', 0)} 个输入单元。" if synthesis_progress else
                    f"DSH 工具调查已保存：{harness.get('model_calls', 0)} 轮模型决策、{harness.get('tool_calls', 0)} 次工具调用；{'调查完成，正在综合复核。' if harness.get('complete') else '继续核对关键证据。'}" if harness else
                    f"来源阅读已保存 {counts['reviewed_source_count']}/{counts['input_source_count']} 条；待审 {counts['pending_source_count']} 条。")
                if generation and note_detail:
                    message += " " + report_note_progress_message(note_detail)
                await asyncio.to_thread(self.store.add_job_event, jid,
                    message)
            budget = params.get("time_budget_seconds", 0)
            report_options = ({"research_context": research_context, "previous_review": previous_review,
                "checkpoint": checkpoint_review, "time_budget_seconds": max(1, budget - 120) if budget else None}
                if research_context or params.get("mode") == "ai" else {})
            report_options.update(frozen_input=frozen_input, input_checkpoint=checkpoint_input)
            report = await asyncio.wait_for(pipeline.build_report(material, params, **report_options), timeout=budget or None)
            if capture_diagnostics:
                report["snapshot"]["public_body_capture"] = capture_diagnostics
            self.store.update_job(jid, progress=95)
            report["job_id"] = jid
            if self.store.get_job(jid).get("cancel_requested"):
                raise CancelledByUser()
            committing.set()
            saved = await asyncio.to_thread(self.store.save_report, report)
            return {"report_id": saved["id"], "title": saved["title"], "source_count": saved["source_count"], "mode": saved["mode"],
                    "public_body_capture": capture_diagnostics, "research_run_id": params.get("research_run_id", ""),
                    "report_input_snapshot": input_state["value"]}
        raise ValueError("不支持的任务类型。")

    async def _research(self, job):
        from .research_engine import run_research
        from .research_export import checkpoint_payload
        jid, params = job["id"], job["params"]
        run_id = params.get("resume_run_id") or jid
        context = {"run_id": run_id, "result": {"run_id": run_id}}
        self.research_contexts[jid] = context
        resume = self.store.get_research_run(run_id) if params.get("resume_run_id") else None
        if resume:
            self.store.attach_research_job(run_id, jid)
        else:
            self.store.create_research_run(jid, params, run_id=run_id)
        # Registry exclusions are trusted server state, never client params.
        with self.store._transaction() as connection:
            params = {**params, "_excluded_account_keys": self.store._excluded_account_keys(connection)}
        from .account_identity import research_registry_context
        params = {**params, "_registry_account_context": research_registry_context(self.store.list_accounts())}
        self.store.checkpoint_research(run_id, status="running", phase="planning")
        self.store.update_job(jid, progress=5, result={"run_id": run_id})
        self.store.add_job_event(jid, "仅重试 AI 分析：使用已完成采集的保存快照，不打开浏览器或重新采集。" if params.get("_analysis_only") else "已恢复保存的研究前沿；保留已访问账号与证据。" if resume else "研究开始：分组发现、相关性筛选、账号拓展与证据分析；每个分支完成即保存。")
        counts = {"inserted": 0, "duplicates": 0, "rejected": 0}
        analysis_storage_issues = []

        async def progress(event):
            message = event.get("message") or event.get("stage")
            context["activity"] = {"stage": event.get("stage"), "message": message, "account_key": event.get("account_key"), "depth": event.get("depth"), "updated_at": time.time()}
            context["result"]["activity"] = context["activity"]
            await asyncio.to_thread(self.store.update_job, jid, result=context["result"])
            if message:
                await asyncio.to_thread(self.store.add_job_event, jid, str(message)[:500], "warning" if event.get("ok") is False else "info")

        async def checkpoint(event):
            if self.store.get_job(jid).get("cancel_requested"):
                raise CancelledByUser()
            existing = self.store.get_research_run(run_id)
            payload = checkpoint_payload(event, max_edges=params.get("max_edges", 60), existing=existing)
            summary = event.get("summary", {})
            fraction = summary.get("visited_count", 0) / max(1, summary.get("visited_count", 0) + len(event.get("frontier") or []))
            progress_value = 95 if event.get("kind") == "finished" else min(90, 10 + int(fraction * 75))
            materials = payload.get("items", [])
            chunks = [materials[index:index + 1000] for index in range(0, len(materials), 1000)] or [[]]
            for index, chunk in enumerate(chunks):
                if self.store.get_job(jid).get("cancel_requested"):
                    raise CancelledByUser()
                part = {**payload, "items": chunk}
                if index < len(chunks) - 1:
                    # Final analysis belongs to the complete checkpoint. Source
                    # evidence is saved in bounded transactions first, so a
                    # larger branch never exceeds the repository array cap.
                    part.pop("analysis", None)
                    previous = existing or {}
                    old_summary = previous.get("summary") or {}
                    part["summary"] = {**summary, "completed_queries": old_summary.get("completed_queries", []),
                                       "seed_query_stats": old_summary.get("seed_query_stats", {}),
                                       "visited_count": len(previous.get("visited") or []), "collection_complete": False,
                                       "collection_outcome": "running"}
                    part["visited"] = previous.get("visited") or []
                    part["frontier"] = previous.get("frontier") or []
                    part["deferred_frontier"] = previous.get("deferred_frontier") or []
                    for key in ("deferred_count", "deferred_record_count", "deferred_relation_count", "pending_account_work_count",
                                "pending_posts_count", "pending_profile_count", "pending_relation_branch_count",
                                "pending_seed_query_count", "pending_seed_queries", "pending_seed_queries_version"):
                        if key in old_summary:
                            part["summary"][key] = old_summary[key]
                    previous_nodes = {row["account_key"]: row for row in previous.get("nodes") or []}
                    already_visited = set(part["visited"])
                    part["nodes"] = [{**row, "status": previous_nodes.get(row["account_key"], {}).get("status", "queued"),
                                       "pending_work": previous_nodes.get(row["account_key"], {}).get("pending_work", {"profile": True, "posts": True})}
                                     if row["account_key"] not in already_visited and row.get("status") in {"completed", "complete", "visited"}
                                     else row for row in part.get("nodes") or []]
                async def save_part():
                    try:
                        return await asyncio.to_thread(self.store.checkpoint_research, run_id, **part)
                    except ValueError as exc:
                        if part.get("analysis") is None or str(exc) != "保存内容超过大小限制":
                            raise
                        # A resumed run can merge new summaries with historic
                        # observations. Preserve the last valid analysis if
                        # that merged catalogue exceeds the repository limit;
                        # retry this slice without analysis so source evidence
                        # is not rolled back with a summary-storage failure.
                        issue = {"source": "research", "stage": "analysis_storage", "ok": False, "outcome": "analysis_size_budget",
                                 "message": "合并历史分析摘要达到存储预算；当前材料仍已保存，保留上次可核对的分析摘要。此次新增分析摘要未全部写入，请结合资料库复核。"}
                        analysis_storage_issues.append(issue)
                        part.pop("analysis", None)
                        part["summary"] = {**part.get("summary", {}), "analysis_storage": "partial", "analysis_storage_message": issue["message"]}
                        part["diagnostics"] = [*part.get("diagnostics", []), issue]
                        return await asyncio.to_thread(self.store.checkpoint_research, run_id, **part)
                saving = asyncio.create_task(save_part())
                cancellation = None
                try:
                    saved = await asyncio.shield(saving)
                except asyncio.CancelledError as exc:
                    # Join only the committing slice. Account for its saved
                    # records before propagating cancellation; later slices
                    # are never shielded or silently continued.
                    saved = await saving
                    cancellation = exc
                for name in counts:
                    counts[name] += int(saved.get(name, 0))
                part["summary"] = {**part.get("summary", summary), "record_count": saved.get("record_count", part.get("summary", summary).get("record_count", 0))}
                context["result"] = {"run_id": run_id, **counts, "summary": part.get("summary", summary), "diagnostics": part.get("diagnostics", []), "activity": context.get("activity", {})}
                self.store.update_job(jid, progress=progress_value, result=context["result"])
                if cancellation is not None:
                    raise cancellation

        # The engine enforces its elapsed budget across browser and AI calls.
        # This outer guard also bounds unexpected adapter cleanup or callbacks.
        result = await asyncio.wait_for(run_research(params, progress=progress, checkpoint=checkpoint, resume=resume), timeout=(params.get("time_budget_seconds", RESEARCH_TIME_DEFAULT) + 35) if params.get("time_budget_seconds", RESEARCH_TIME_DEFAULT) else None)
        graph = self.store.research_graph(run_id, limit=300)
        summary = result.get("summary", {})
        diagnostics = [*result.get("diagnostics", []), *analysis_storage_issues]
        if analysis_storage_issues:
            summary = {**summary, "analysis_storage": "partial", "analysis_storage_message": analysis_storage_issues[-1]["message"]}
        failures = [row for row in diagnostics if row.get("ok") is False]
        required_analysis_failed = params.get("analysis_mode") == "ai" and result.get("analysis", {}).get("ai", {}).get("requested") and not result.get("analysis", {}).get("ai", {}).get("used")
        if summary.get("outcome") == "failed" or summary.get("stop_reason") == "no_readable_evidence" or (not graph.get("items") and not graph.get("nodes") and diagnostics and len(failures) == len(diagnostics)):
            error = SourceCollectionError("研究入口未取得可核验内容；分支错误与断点已保留，请查看研究详情后继续。", diagnostics)
            error.result["run_id"] = run_id
            raise error
        return {"run_id": run_id, **counts, "fetched": summary.get("record_count", len(graph.get("items", []))), "nodes": summary.get("account_count", len(graph.get("nodes", []))),
                "edges": summary.get("edge_count", len(graph.get("edges", []))), "claims": len(graph.get("claims", [])), "summary": summary,
                "diagnostics": diagnostics, "ai": result.get("analysis", {}).get("ai", {}),
                "_status": "partial" if required_analysis_failed or failures or summary.get("outcome") in {"time_budget", "record_budget", "account_budget", "edge_budget", "partial", "partial_error", "blocked"} or summary.get("stop_reason") in {"time_budget", "record_budget", "account_budget", "edge_budget"} or counts["rejected"] else "succeeded"}
