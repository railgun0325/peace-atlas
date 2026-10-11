"""Saved nested payloads reach the real job checkpoint callback unchanged."""
import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

from app.workbench.report_progress import report_note_progress, report_note_progress_message


def note_progress():
    return {"stage": "evidence_notes", "level": 1, "planned_batch_count": 3,
            "current_batch_number": 2, "completed_unit_count": 200, "total_unit_count": 601,
            "model_request_count": 11, "completed_request_count": 9,
            "reused_reviewed_batch_count": 0, "coverage_is_independent_truth_verification": False}


def review_checkpoint():
    return {"coverage_progress": {"input_source_count": 601, "reviewed_source_count": 601, "pending_source_count": 0},
            "synthesis_state": {"complete": True}, "progress": None,
            "report_generation": {"phase": "review_pending", "complete": False,
                "attempts": [{}, {"source_review_input_synthesis": {"complete": False, "progress": note_progress()}}]}}


def test_saved_legacy_checkpoint_remains_readable_without_mutation():
    saved = {"progress": note_progress()}
    before = copy.deepcopy(saved)
    result = report_note_progress(saved)
    assert result["scope"] == "legacy" and result["progress"]["completed_unit_count"] == 200
    result["progress"]["completed_unit_count"] = 999
    assert saved == before


def test_final_review_uses_latest_attempt_counters_not_old_flat_or_base_notes():
    state = review_checkpoint()
    state["progress"] = {**note_progress(), "completed_unit_count": 601}
    state["synthesis_state"]["progress"] = state["progress"]
    detail = report_note_progress(state)
    assert detail["scope"] == "source_review" and detail["progress"] == note_progress()
    text = report_note_progress_message(detail)
    assert "报告来源复核笔记" in text and "2/3" in text and "200/601" in text and "9/11" in text


def test_initial_notes_without_generation_use_saved_synthesis_progress():
    state = {"synthesis_state": {"complete": False, "progress": note_progress()}}
    assert report_note_progress(state)["scope"] == "synthesis"
    assert "证据综合笔记正在处理" in report_note_progress_message(report_note_progress(state))


def test_no_note_stage_does_not_invent_counters_or_reuse_an_older_attempt():
    state = review_checkpoint()
    state["report_generation"]["attempts"].append({})
    assert report_note_progress(state) is None
    assert report_note_progress_message(None) is None
    assert report_note_progress({"progress": [], "synthesis_state": {"progress": {}}}) is None


def test_complete_generation_does_not_describe_stale_notes_as_running():
    for terminal in ({"complete": True}, {"phase": "complete"}, {"phase": "completed"}):
        state = review_checkpoint()
        state["report_generation"].update(terminal)
        assert report_note_progress(state) is None


def test_real_checkpoint_callback_preserves_payload_and_records_final_note_counters():
    source = Path(__file__).parents[1] / "app/workbench/jobs.py"
    node = next(node for node in ast.walk(ast.parse(source.read_text("utf-8-sig")))
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "checkpoint_review")
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    records, events = [], []
    class Store:
        def update_job(self, jid, **fields): records.append(copy.deepcopy(fields))
        def get_job(self, jid): return {"cancel_requested": False}
        def add_job_event(self, jid, message): events.append(message)
    environment = {"asyncio": asyncio, "self": SimpleNamespace(store=Store()), "jid": "owned-job",
                   "params": {"research_run_id": "saved-run"}, "report_note_progress": report_note_progress,
                   "report_note_progress_message": report_note_progress_message}
    exec(compile(module, str(source), "exec"), environment)
    state = review_checkpoint()
    before = copy.deepcopy(state)
    asyncio.run(environment["checkpoint_review"](state))
    assert records[-1]["result"]["report_checkpoint"] == before and state == before
    assert records[-1]["progress"] == 88  # Coarse phase percentage, never invented per-request percent.
    assert "200/601" in events[-1] and "2/3" in events[-1] and "9/11" in events[-1]
    assert "报告来源复核笔记" in events[-1]
    state["report_generation"].update(complete=True, phase="complete")
    asyncio.run(environment["checkpoint_review"](state))
    assert records[-1]["progress"] == 92
    assert "正在处理" not in events[-1] and "正在完成报告" in events[-1]
