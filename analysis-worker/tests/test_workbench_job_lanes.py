"""Separate login/normal execution, with real temporary DB and no platform calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
from datetime import datetime
from uuid import UUID

import pytest

from app.workbench import pipeline, social
from app.workbench.jobs import JobRunner
from app.workbench.store import Store
from app.workbench import store as store_module


def test_same_clock_tick_keeps_enqueue_order_across_store_restart(tmp_path, monkeypatch):
    path = tmp_path / 'same-clock.sqlite3'
    store = Store(path)
    monkeypatch.setattr(store_module, '_now', lambda: '2026-10-09T00:00:00.000000Z')
    identities = iter([UUID(int=900), UUID(int=2), UUID(int=1)])
    monkeypatch.setattr(store_module, 'uuid4', lambda: next(identities))
    first = store.create_job('login', {'platform':'twitter'})
    store.close()
    restarted = Store(path)
    second = restarted.create_job('login', {'platform':'instagram'})
    normal = restarted.create_job('collect', {'source':'rss'})
    assert datetime.fromisoformat(first['created_at'].replace('Z','+00:00')) < datetime.fromisoformat(second['created_at'].replace('Z','+00:00'))
    assert restarted.claim_job(kind='login')['id'] == first['id']
    assert restarted.claim_job(exclude_kind='login')['id'] == normal['id']
    assert restarted.claim_job(kind='login')['id'] == second['id']
    restarted.close()


def test_same_clock_concurrent_enqueues_follow_committed_database_order(tmp_path, monkeypatch):
    path = tmp_path / 'concurrent-clock.sqlite3'
    stores = [Store(path), Store(path)]
    monkeypatch.setattr(store_module, '_now', lambda: '2026-10-09T00:00:00.000000Z')
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda index: stores[index % 2].create_job('collect', {'number':index}),range(24)))
    with stores[0]._transaction() as connection:
        committed = [row['id'] for row in connection.execute('SELECT id FROM wb_jobs ORDER BY rowid')]
    claimed = [stores[index % 2].claim_job()['id'] for index in range(24)]
    assert claimed == committed
    assert len({job['created_at'] for job in created}) == 24
    assert stores[0].claim_job() is None
    for store in stores: store.close()


def test_old_timestamp_formats_can_enqueue_and_keep_saved_records(tmp_path, monkeypatch):
    store = Store(tmp_path/'older-dates.sqlite3')
    first = store.create_job('collect', {})
    second = store.create_job('collect', {})
    with store._transaction(write=True) as connection:
        connection.execute('UPDATE wb_jobs SET created_at=? WHERE id=?',('2026-10-09T00:00:00Z',first['id']))
        connection.execute('UPDATE wb_jobs SET created_at=? WHERE id=?',('2026-10-09 00:00:00',second['id']))
    monkeypatch.setattr(store_module,'_now',lambda:'2026-10-09T00:00:00.000000Z')
    newer = [store.create_job('collect',{}) for _ in range(4)]
    assert store.get_job(first['id'])['created_at'] == '2026-10-09T00:00:00Z'
    assert store.get_job(second['id'])['created_at'] == '2026-10-09 00:00:00'
    assert [store.claim_job()['id'] for _ in range(6)] == [first['id'],second['id']]+[row['id'] for row in newer]
    assert len({job['created_at'] for job in newer}) == 4
    store.close()


def test_postgres_enqueue_serializes_timestamp_read_and_insert(tmp_path,monkeypatch):
    store = Store(tmp_path/'pg-enqueue-contract.sqlite3')
    calls, committed = [], []

    class Cursor:
        def __init__(self,rows): self.rows=rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class Connection:
        def execute(self,sql,values):
            calls.append((sql,values))
            if sql.startswith('SELECT pg_advisory_xact_lock'):
                return Cursor([])
            if 'WHERE created_at LIKE' in sql:
                return Cursor([{'created_at':committed[-1]}] if committed else [])
            if 'WHERE created_at NOT LIKE' in sql:
                return Cursor([])
            assert sql.lstrip().startswith('INSERT INTO wb_jobs'),sql
            committed.append(values[7]); return Cursor([])

    @contextmanager
    def transaction(*,write=False):
        assert write
        yield Connection()

    monkeypatch.setattr(store,'_postgres',True)
    monkeypatch.setattr(store,'_transaction',transaction)
    monkeypatch.setattr(store_module,'_now',lambda:'2026-10-09T00:00:00.000000Z')
    first=store.create_job('collect',{}); second=store.create_job('report',{})
    assert first['created_at'] < second['created_at']
    assert [sql.split('(')[0] for sql,_ in calls if sql.startswith('SELECT pg_advisory_xact_lock')] == ['SELECT pg_advisory_xact_lock']*2
    assert calls[0][1] == calls[4][1] and isinstance(calls[0][1][0],int)
    # This checks the SQL contract, not a running PostgreSQL service.
    store._postgres=False; store.close()


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def test_filtered_claims_remain_fifo_and_atomic(tmp_path):
    store = Store(tmp_path / "filtered.sqlite3")
    login = store.create_job("login", {"platform": "twitter"})
    normal = store.create_job("collect", {})
    assert store.claim_job(exclude_kind="login")["id"] == normal["id"]
    assert store.claim_job(exclude_kind="login") is None
    assert store.claim_job(kind="login")["id"] == login["id"]
    created = [store.create_job(kind, {}) for kind in ("login", "collect", "report") for _ in range(8)]

    def claim(index):
        login_lane = index % 2 == 0
        job = store.claim_job(**({"kind": "login"} if login_lane else {"exclude_kind": "login"}))
        if job:
            assert (job["kind"] == "login") is login_lane
        return job

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [job for job in pool.map(claim, range(48)) if job]
    assert {job["id"] for job in claimed} == {job["id"] for job in created}
    assert len(claimed) == len({job["id"] for job in claimed}) == 24
    assert store.claim_job() is None
    first = store.create_job("login", {})
    second = store.create_job("report", {})
    assert store.claim_job()["id"] == first["id"]  # Default remains unfiltered FIFO.
    assert store.claim_job()["id"] == second["id"]


def test_postgres_filtered_claim_uses_bound_value_and_row_lock(tmp_path, monkeypatch):
    store = Store(tmp_path / "postgres-contract.sqlite3")
    calls = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, sql, values):
            calls.append((sql, values))
            return Cursor({"id": "claimed", "status": "running"})

    @contextmanager
    def transaction(*, write=False):
        assert write
        yield Connection()

    monkeypatch.setattr(store, "_postgres", True)
    monkeypatch.setattr(store, "_transaction", transaction)
    monkeypatch.setattr(store, "_job", lambda row: row)
    assert store.claim_job(kind="login")["id"] == "claimed"
    assert "AND kind = %s" in calls[0][0]
    assert calls[0][0].endswith("FOR UPDATE SKIP LOCKED")
    assert calls[0][1] == ["login"]
    assert "AND status = 'queued'" in calls[1][0]
    with pytest.raises(ValueError):
        store.claim_job(kind="login' OR 1=1")


def test_waiting_login_does_not_block_instagram_and_both_logins_can_cancel(tmp_path, monkeypatch):
    store = Store(tmp_path / "login-independent.sqlite3")
    first = store.create_job("login", {"platform": "twitter", "method": "manual"})
    second = store.create_job("login", {"platform": "instagram", "method": "manual"})
    collected = store.create_job("collect", {"source": "instagram"})
    runner = JobRunner(store)

    async def scenario():
        started, closed = [], []
        login_entered = asyncio.Event()

        async def login(platform):
            started.append(platform)
            login_entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(platform)

        async def collect(*args, **kwargs):
            # Either lane may win the scheduling race. Exercise collection
            # completion specifically while the login is waiting for a user.
            await login_entered.wait()
            return {"items": [], "accounts": [], "relations": [], "diagnostics": [{"ok": True}]}

        monkeypatch.setattr(social, "login", login)
        monkeypatch.setattr(social, "collect", collect)
        await runner.start()
        try:
            await until(lambda: store.get_job(collected["id"])["status"] == "succeeded")
            assert started == ["twitter"]
            assert store.get_job(first["id"])["status"] == "running"
            assert store.get_job(second["id"])["status"] == "queued"
            store.request_cancel(first["id"])
            await until(lambda: "instagram" in started)
            assert closed == ["twitter"]
            store.request_cancel(second["id"])
            await until(lambda: store.get_job(second["id"])["status"] == "cancelled")
            assert closed == ["twitter", "instagram"]
            assert store.get_job(first["id"])["status"] == "cancelled"
        finally:
            await runner.stop()
        assert runner.task.done() and runner.login_task.done()
        assert store.overview()["stats"]["active_jobs"] == 0

    asyncio.run(scenario())


def test_shutdown_closes_both_inflight_lanes(tmp_path, monkeypatch):
    store = Store(tmp_path / "dual-shutdown.sqlite3")
    jobs = [store.create_job("login", {"platform": "twitter", "method": "manual"}), store.create_job("collect", {"source": "rss"})]
    runner = JobRunner(store)

    async def scenario():
        started, closed = set(), set()

        async def pending(name):
            started.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                closed.add(name)

        async def login(*args):
            return await pending("login")

        async def collect(*args):
            return await pending("collect")

        monkeypatch.setattr(social, "login", login)
        monkeypatch.setattr(pipeline, "collect", collect)
        await runner.start()
        await until(lambda: started == {"login", "collect"})
        await asyncio.wait_for(runner.stop(), timeout=3)
        await runner.stop()  # Idempotent, with no second cancellation of cleanup.
        assert closed == {"login", "collect"}
        assert runner.task.done() and runner.login_task.done()

    asyncio.run(scenario())
    assert all(store.get_job(job["id"])["status"] == "interrupted" for job in jobs)
    assert store.overview()["stats"]["active_jobs"] == 0


@pytest.mark.parametrize("commit_fails", [False, True])
def test_login_closes_while_shutdown_joins_normal_commit(tmp_path, monkeypatch, commit_fails):
    store = Store(tmp_path / "dual-commit.sqlite3")
    login = store.create_job("login", {})
    normal = store.create_job("collect", {})
    runner = JobRunner(store)

    async def scenario():
        started, closed = set(), set()
        release = asyncio.Event()

        async def perform(job, committing):
            started.add(job["kind"])
            if job["kind"] == "login":
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.add("login")
            committing.set()
            await release.wait()
            if commit_fails:
                raise OSError("injected commit failure")
            return {"inserted": 1}

        monkeypatch.setattr(runner, "_perform", perform)
        await runner.start()
        await until(lambda: started == {"login", "collect"})
        stopping = asyncio.create_task(runner.stop())
        try:
            await until(lambda: "login" in closed)
            assert not stopping.done()
        finally:
            release.set()
            await asyncio.wait_for(stopping, timeout=3)

    asyncio.run(scenario())
    assert store.get_job(login["id"])["status"] == "interrupted"
    assert store.get_job(normal["id"])["status"] == ("failed" if commit_fails else "succeeded")
    assert store.overview()["stats"]["active_jobs"] == 0


def test_shutdown_joins_pending_claims_before_returning(tmp_path, monkeypatch):
    store = Store(tmp_path / "claim-shutdown.sqlite3")
    jobs = [store.create_job("login", {}), store.create_job("collect", {})]
    runner = JobRunner(store)
    claim = store.claim_job
    entered = {"login": threading.Event(), "normal": threading.Event()}
    release = threading.Event()
    performed = []

    def paused_claim(**filters):
        job = claim(**filters)
        entered["login" if filters.get("kind") == "login" else "normal"].set()
        if not release.wait(timeout=3):
            raise TimeoutError("fixture did not release claim")
        return job

    async def perform(job, committing):
        performed.append(job["id"])
        return {}

    monkeypatch.setattr(store, "claim_job", paused_claim)
    monkeypatch.setattr(runner, "_perform", perform)

    async def scenario():
        await runner.start()
        await until(lambda: all(event.is_set() for event in entered.values()))
        stopping = asyncio.create_task(runner.stop())
        try:
            await asyncio.sleep(0.02)
            assert not stopping.done()
        finally:
            release.set()
            await asyncio.wait_for(stopping, timeout=3)
        assert runner.task.done() and runner.login_task.done()

    asyncio.run(scenario())
    assert performed == []
    assert all(store.get_job(job["id"])["status"] == "interrupted" for job in jobs)
    assert store.overview()["stats"]["active_jobs"] == 0
