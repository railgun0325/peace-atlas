"""Atomic task series with truthful aggregation and targeted retries."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from uuid import uuid4
from .limits import BATCH_RECORDS_MAX, BATCH_TASKS_MAX, record_ceiling


RETRYABLE = {"partial", "failed", "cancelled", "interrupted"}
HARD_RESEARCH_BUDGETS = {"record_budget", "account_budget", "edge_budget"}


def is_retryable_job(job: dict) -> bool:
    """A hard research cap cannot advance by repeating the same parameters."""
    if job.get("status") not in RETRYABLE:
        return False
    result = job.get("result") or {}
    summary = result.get("summary") or {}
    return not (job.get("status") == "partial" and summary.get("stop_reason") in HARD_RESEARCH_BUDGETS)


class ResearchBatches:
    def _initialize_batches(self):
        with self._transaction(write=True) as connection:
            self._execute(connection, """CREATE TABLE IF NOT EXISTS wb_job_batches (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, total_records INTEGER NOT NULL,
                parent_batch_id TEXT REFERENCES wb_job_batches(id), created_at TEXT NOT NULL)""")
            self._execute(connection, """CREATE TABLE IF NOT EXISTS wb_job_batch_jobs (
                batch_id TEXT NOT NULL REFERENCES wb_job_batches(id), job_id TEXT NOT NULL UNIQUE REFERENCES wb_jobs(id),
                position INTEGER NOT NULL, target_records INTEGER NOT NULL, retry_of_job_id TEXT REFERENCES wb_jobs(id),
                PRIMARY KEY(batch_id,job_id), UNIQUE(batch_id,position))""")
            if self._postgres:
                self._execute(connection, "ALTER TABLE wb_job_batch_jobs ADD COLUMN IF NOT EXISTS retry_of_job_id TEXT REFERENCES wb_jobs(id)")
            elif not any(row["name"] == "retry_of_job_id" for row in connection.execute("PRAGMA table_info(wb_job_batch_jobs)")):
                connection.execute("ALTER TABLE wb_job_batch_jobs ADD COLUMN retry_of_job_id TEXT REFERENCES wb_jobs(id)")
            self._execute(connection, "CREATE INDEX IF NOT EXISTS wb_batches_created ON wb_job_batches(created_at,id)")
            self._execute(connection, "CREATE INDEX IF NOT EXISTS wb_batch_retries ON wb_job_batch_jobs(retry_of_job_id)")

    @staticmethod
    def _validate_batch(title, tasks, total_records):
        from .research_repository import _public_json
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("任务串名称必须在 1 至 200 字之间")
        if isinstance(total_records, bool) or not isinstance(total_records, int) or not 1 <= total_records <= BATCH_RECORDS_MAX:
            raise ValueError(f"任务串总体材料目标必须在 1 至 {BATCH_RECORDS_MAX} 之间")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= BATCH_TASKS_MAX:
            raise ValueError(f"每个任务串需要 1 至 {BATCH_TASKS_MAX} 个子任务")
        prepared = []
        for task in tasks:
            if not isinstance(task, dict) or task.get("kind") not in {"collect", "research"} or not isinstance(task.get("params"), dict):
                raise ValueError("任务串只能包含 collect 或 research 参数对象")
            params = dict(task["params"])
            target = params.get("max_records", 50)
            ceiling = record_ceiling(task["kind"])
            if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= ceiling:
                raise ValueError(f"子任务材料上限必须在 1 至 {ceiling} 之间")
            _public_json(params)
            prepared.append({"kind": task["kind"], "params": params, "target_records": target})
        return prepared

    def _create_batch(self, connection, title, tasks, total_records, parent_batch_id=None):
        from .store import _now
        batch_id, stamp = str(uuid4()), _now()
        self._execute(connection, "INSERT INTO wb_job_batches (id,title,total_records,parent_batch_id,created_at) VALUES (?,?,?,?,?)",
                      (batch_id, title.strip(), total_records, parent_batch_id, stamp))
        previous_created = None
        for position, task in enumerate(tasks):
            job = self.create_job(task["kind"], task["params"], _connection=connection)
            # A Windows clock tick can give several atomic inserts the same
            # time. Preserve the planned order in the existing FIFO queue,
            # whose final tiebreaker is a random UUID.
            if previous_created and job["created_at"] <= previous_created:
                created = datetime.fromisoformat(previous_created.replace("Z", "+00:00")) + timedelta(microseconds=1)
                job["created_at"] = created.isoformat(timespec="microseconds").replace("+00:00", "Z")
                self._execute(connection, "UPDATE wb_jobs SET created_at=? WHERE id=?", (job["created_at"], job["id"]))
            previous_created = job["created_at"]
            self._execute(connection, "INSERT INTO wb_job_batch_jobs (batch_id,job_id,position,target_records,retry_of_job_id) VALUES (?,?,?,?,?)",
                          (batch_id, job["id"], position, task["target_records"], task.get("retry_of_job_id")))
            if task["kind"] == "research" and task["params"].get("resume_run_id"):
                run_id = self._run_id(connection, task["params"]["resume_run_id"])
                if not run_id:
                    raise ValueError("待续查研究不存在，未创建任何子任务")
                self._attach_research_job(connection, run_id, job["id"], stamp)
        return batch_id

    def create_job_batch(self, title: str, tasks: list[dict], total_records: int) -> dict:
        prepared = self._validate_batch(title, tasks, total_records)
        with self._transaction(write=True) as connection:
            batch_id = self._create_batch(connection, title, prepared, total_records)
        return self.get_job_batch(batch_id)

    def _batch_job_retryable(self, connection, job, *, parents=None):
        if not self._job_retryable(connection, job):
            return False
        _, latest = self._retry_branch(connection, job["id"], parents=parents)
        return self._job_retryable(connection, latest)

    def _batch_record(self, connection, id, *, lock=False):
        batch = self._execute(connection, "SELECT * FROM wb_job_batches WHERE id=?", (id,)).fetchone()
        if batch is None:
            return None
        value = dict(batch)
        suffix = " FOR UPDATE OF j" if self._postgres and lock else ""
        rows = self._execute(connection, "SELECT j.*,bj.position,bj.target_records,bj.retry_of_job_id FROM wb_job_batch_jobs bj JOIN wb_jobs j ON j.id=bj.job_id WHERE bj.batch_id=? ORDER BY bj.position" + suffix, (id,)).fetchall()
        jobs = [self._job(row) for row in rows]
        counts = dict(Counter(job["status"] for job in jobs))
        active = counts.get("running", 0) + counts.get("queued", 0)
        if counts.get("running"):
            status = "running"
        elif counts.get("queued"):
            status = "queued"
        elif jobs and counts.get("succeeded", 0) == len(jobs):
            status = "succeeded"
        elif jobs and counts.get("cancelled", 0) == len(jobs):
            status = "cancelled"
        elif jobs and counts.get("failed", 0) == len(jobs):
            status = "failed"
        elif jobs and counts.get("interrupted", 0) == len(jobs):
            status = "interrupted"
        else:
            status = "partial"
        saved = sum(max(0, int((job.get("result") or {}).get("inserted", 0) or 0)) for job in jobs)
        duplicates = sum(max(0, int((job.get("result") or {}).get("duplicates", 0) or 0)) for job in jobs)
        parents = self._retry_index(connection)
        value.update(jobs=jobs, tasks=jobs, status=status, counts=counts, task_count=len(jobs), active_count=active,
                     completed_count=len(jobs) - active, succeeded_count=counts.get("succeeded", 0),
                     retryable_count=sum(self._batch_job_retryable(connection, job, parents=parents) for job in jobs),
                     progress=round(sum(job["progress"] for job in jobs) / len(jobs), 1) if jobs else 0,
                     records_saved=saved, duplicates=duplicates, target_reached=saved >= value["total_records"],
                     planned_records=sum(job["target_records"] for job in jobs))
        return value

    def get_job_batch(self, id: str) -> dict | None:
        with self._transaction() as connection:
            return self._batch_record(connection, id)

    def list_job_batches(self, page: int = 1, page_size: int = 25) -> dict:
        from .store import _pagination
        page, page_size = _pagination(page, page_size)
        with self._transaction() as connection:
            total = self._execute(connection, "SELECT COUNT(*) AS total FROM wb_job_batches").fetchone()["total"]
            ids = self._execute(connection, "SELECT id FROM wb_job_batches ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
            items = [self._batch_record(connection, row["id"]) for row in ids]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def cancel_job_batch(self, id: str) -> dict:
        from .store import _now
        with self._transaction(write=True) as connection:
            batch = self._batch_record(connection, id, lock=True)
            if batch is None:
                raise LookupError("任务串不存在")
            cancelled = 0
            for job in batch["jobs"]:
                if job["status"] == "queued":
                    cursor = self._execute(connection, "UPDATE wb_jobs SET status='cancelled',cancel_requested=1,finished_at=? WHERE id=? AND status='queued'", (_now(), job["id"]))
                    if cursor.rowcount:
                        self._execute(connection, "UPDATE wb_research_runs SET status='cancelled',updated_at=? WHERE current_job_id=?", (_now(), job["id"]))
                        self._event(connection, job["id"], "任务串取消：子任务尚未执行。", "warning")
                        cancelled += 1
                elif job["status"] == "running" and not job["cancel_requested"]:
                    cursor = self._execute(connection, "UPDATE wb_jobs SET cancel_requested=1 WHERE id=? AND status='running' AND cancel_requested=0", (job["id"],))
                    if cursor.rowcount:
                        self._event(connection, job["id"], "任务串取消：等待当前批次保存后停止。", "warning")
                        cancelled += 1
            result = self._batch_record(connection, id)
            result["cancel_requested_count"] = cancelled
        return result

    def retry_job_batch(self, id: str) -> dict:
        with self._transaction(write=True) as connection:
            batch = self._batch_record(connection, id)
            if batch is None:
                raise LookupError("任务串不存在")
            # Use the same roots and lock order as individual retries. Locking
            # descendants first could deadlock a concurrent ancestor retry.
            parents = self._lock_retry_roots(connection, [job["id"] for job in batch["jobs"]])
            tasks = []
            for job in batch["jobs"]:
                if not self._batch_job_retryable(connection, job, parents=parents):
                    continue
                root, latest = self._retry_branch(connection, job["id"], parents=parents)
                params = self._retry_params(connection, latest, root)
                tasks.append({"kind": latest["kind"], "params": params, "target_records": job["target_records"], "retry_of_job_id": latest["id"]})
            if not tasks:
                from .job_retries import RetryConflict
                for job in batch["jobs"]:
                    if is_retryable_job(job):
                        _, latest = self._retry_branch(connection, job["id"], parents=parents)
                        if latest["status"] in {"queued", "running"}:
                            raise RetryConflict("此任务串的重试已排队或正在运行，请查看现有任务。")
                raise ValueError("当前没有可重试的子任务；已成功或仍在执行的任务不会重复创建")
            target = min(10000, sum(task["target_records"] for task in tasks))
            batch_id = self._create_batch(connection, batch["title"][:185] + " · 重试", tasks, target, parent_batch_id=id)
            result = self._batch_record(connection, batch_id)
        return result
