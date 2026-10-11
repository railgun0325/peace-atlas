"""Bounded deferred replacement preserves validation, bodies and transactions."""
import copy
import hashlib
import json
import sqlite3

import pytest

from app.workbench.social.common import account
from app.workbench.store import Store


STAMP = "2026-10-09T06:00:00.000000Z"
SOFT_BYTES = 1048576


class ObservedStore(Store):
    def __init__(self, *args, **kwargs):
        self.observing = False
        self.insert_attempts = []
        self.successful_inserts = []
        self.deletes = []
        self.actual_insert_statements = 0
        self.fail_insert_number = None
        super().__init__(*args, **kwargs)

    def _connect(self):
        connection = super()._connect()
        if self.observing:
            def trace(sql):
                if sql.startswith("INSERT INTO wb_research_deferred"):
                    self.actual_insert_statements += 1
            connection.set_trace_callback(trace)
        return connection

    def _execute(self, connection, sql, params=()):
        if self.observing and sql.startswith("DELETE FROM wb_research_deferred"):
            self.deletes.append(sql)
        insert = self.observing and sql.startswith("INSERT INTO wb_research_deferred")
        if insert:
            row = {"params": len(params), "rows": len(params) // 6,
                "payload_bytes": sum(len(str(value).encode("utf-8")) + 8 for value in params) + (len(params) // 6) * 16,
                "record_bytes": [len(params[index].encode("utf-8")) for index in range(4, len(params), 6)]}
            self.insert_attempts.append(row)
            if len(self.insert_attempts) == self.fail_insert_number:
                raise RuntimeError("Owned failure before second INSERT")
        result = super()._execute(connection, sql, params)
        if insert:
            self.successful_inserts.append(row)
        return result

    def observe(self):
        self.insert_attempts.clear()
        self.successful_inserts.clear()
        self.deletes.clear()
        self.actual_insert_statements = 0
        self.observing = True


@pytest.fixture
def store(tmp_path):
    value = ObservedStore(tmp_path / "deferred-chunks.sqlite3")
    job = value.create_job("research", {"analysis_mode": "rules", "source": "twitter"})
    value.create_research_run(job["id"], job["params"])
    value.update_job(job["id"], status="partial")
    value.run_id = job["id"]
    yield value
    value.close()


def candidate(handle="child0", item_count=1, body="Synthetic intact public body"):
    source = "https://x.com/parent/status/101"
    return {"account_key": "twitter:" + handle, "account": account("twitter", handle),
        "depth": 1, "parent_key": "twitter:parent", "score": 20,
        "status": "deferred", "admission_state": "deferred", "defer_reason": "account_budget",
        "observed_at": STAMP, "evidence_urls": [source],
        "discovery_paths": [{"parent_key": "twitter:parent", "reason": "following", "depth": 1,
                             "evidence_urls": [source], "observed_at": STAMP}],
        "relations": [], "items": [{"account_key": "twitter:" + handle, "type": "social",
            "platform": "twitter", "url": f"https://x.com/{handle}/status/{1000 + index}",
            "title": f"Synthetic public material {index}", "content": body + f" [{index}]",
            "published_at": STAMP, "metadata": {"content_kind": "body"}} for index in range(item_count)]}


def replace(store, entries, stamp=STAMP):
    with store._transaction(write=True) as connection:
        store._checkpoint_deferred(connection, store.run_id, entries, stamp)


def read(store):
    with store._transaction() as connection:
        return store._read_deferred(connection, store.run_id)


def database_rows(store):
    with sqlite3.connect(store.path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: sorted(connection.execute('SELECT * FROM "' + table + '"').fetchall(), key=repr) for table in tables}


def assert_integrity(store):
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("item_count", [101, 205])
def test_more_than_100_rows_use_actual_bounded_insert_statements_and_keep_ordinals(store, item_count):
    entry = candidate(item_count=item_count)
    store.observe()
    replace(store, [entry])
    total_rows = item_count + 3  # Header, one path and one evidence URL.
    assert store.actual_insert_statements == (total_rows + 99) // 100
    assert sum(row["rows"] for row in store.successful_inserts) == total_rows
    assert all(row["params"] <= 600 and row["rows"] <= 100 for row in store.successful_inserts)
    assert read(store) == [entry]
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute("SELECT ordinal,record,updated_at FROM wb_research_deferred WHERE component='items' ORDER BY ordinal").fetchall()
    assert [row[0] for row in rows] == list(range(item_count))
    assert [json.loads(row[1]) for row in rows] == entry["items"]
    assert {row[2] for row in rows} == {STAMP}
    assert_integrity(store)


def test_chunks_split_on_utf8_payload_before_the_row_count_limit(store):
    entry = candidate(item_count=3, body="字" * 200000)
    store.observe()
    replace(store, [entry])
    assert store.actual_insert_statements == 3
    assert all(row["payload_bytes"] <= SOFT_BYTES for row in store.successful_inserts)
    assert all(row["params"] <= 600 for row in store.successful_inserts)
    assert read(store) == [entry]
    assert_integrity(store)


def test_legal_single_body_above_soft_chunk_limit_remains_complete(store):
    entry = candidate(body="字" * 500000)
    expected = hashlib.sha256(entry["items"][0]["content"].encode("utf-8")).hexdigest()
    store.observe()
    replace(store, [entry])
    large = [row for row in store.successful_inserts if row["payload_bytes"] > SOFT_BYTES]
    assert len(large) == 1 and large[0]["rows"] == 1
    assert large[0]["record_bytes"][0] <= 2097152
    actual = read(store)[0]["items"][0]
    assert actual == entry["items"][0]
    assert hashlib.sha256(actual["content"].encode("utf-8")).hexdigest() == expected
    assert_integrity(store)


def test_second_chunk_failure_rolls_back_complete_database_state(store):
    replace(store, [candidate(body="Original checkpoint retained")])
    before = database_rows(store)
    store.observe()
    store.fail_insert_number = 2
    with pytest.raises(RuntimeError, match="second INSERT"):
        replace(store, [candidate(item_count=205)], stamp="2026-10-09T07:00:00.000000Z")
    assert store.actual_insert_statements == 1
    assert len(store.successful_inserts) == 1
    assert database_rows(store) == before
    assert_integrity(store)


@pytest.mark.parametrize("invalid", ["oversize", "secret", "depth", "bad_depth", "admitted", "duplicate", "body_type"])
def test_invalid_last_record_is_rejected_before_delete_or_insert(store, invalid):
    replace(store, [candidate(body="Original checkpoint retained")])
    before = database_rows(store)
    entries = [candidate("child0", item_count=205), candidate("child1")]
    if invalid == "oversize":
        entries[-1]["items"][0]["content"] = "字" * 700000
    elif invalid == "secret":
        entries[-1]["items"][0]["metadata"]["api_key"] = "OWNED_INVALID_SYNTHETIC_FIELD"
    elif invalid == "depth":
        nested = "Public synthetic value"
        for _ in range(25):
            nested = {"public": nested}
        entries[-1]["items"][0]["metadata"]["nested"] = nested
    elif invalid == "bad_depth":
        entries[-1]["depth"] = True
    elif invalid == "admitted":
        entries[-1]["admission_state"] = "admitted"
    elif invalid == "duplicate":
        entries.append(copy.deepcopy(entries[0]))
    else:
        entries[-1]["items"][0]["content"] = {"unexpected": "object"}
    store.observe()
    with pytest.raises(ValueError):
        replace(store, entries)
    assert not store.deletes and not store.insert_attempts and not store.actual_insert_statements
    assert database_rows(store) == before


def test_reorder_remove_and_empty_replacements_preserve_global_header_order(store):
    entries = [candidate(f"child{index}", item_count=3) for index in range(5)]
    replace(store, entries)
    expected = [entries[4], entries[1]]
    replace(store, expected, stamp="2026-10-09T07:00:00.000000Z")
    assert read(store) == expected
    with sqlite3.connect(store.path) as connection:
        headers = connection.execute("SELECT account_key,ordinal FROM wb_research_deferred WHERE component='header' ORDER BY ordinal").fetchall()
    assert headers == [("twitter:child4", 0), ("twitter:child1", 1)]
    replace(store, [])
    assert read(store) == []
    assert_integrity(store)


def test_admitted_entry_with_independent_material_requires_and_keeps_saved_node(store):
    store.checkpoint_research(store.run_id, accounts=[account("twitter", "child0")],
        nodes=[{"account_key": "twitter:child0", "depth": 0, "status": "visited", "score": 20}])
    entry = candidate()
    entry.update(admission_state="admitted", discovery_paths=[], evidence_urls=[])
    replace(store, [entry])
    assert read(store) == [entry]
    assert_integrity(store)


def test_public_account_and_material_url_aliases_remain_valid_without_rewriting_body(store):
    entry = candidate(body="Synthetic complete public alias source")
    entry["account"]["url"] = entry["account"]["url"].replace("x.com/", "twitter.com/")
    entry["items"][0]["url"] = entry["items"][0]["url"].replace("x.com/", "twitter.com/")
    replace(store, [entry])
    assert read(store) == [entry]
    assert_integrity(store)
