"""Stop conditions must be explicit integer choices, not implicit bool/string coercion."""
import pytest
from pydantic import ValidationError
from app.workbench.server import ReportParams


@pytest.mark.parametrize('field', ['harness_max_model_calls', 'harness_max_tool_calls', 'time_budget_seconds'])
@pytest.mark.parametrize('value', [False, True, '0', '60', 0.0, 60.0, None])
def test_report_stop_conditions_reject_implicit_values(field, value):
    with pytest.raises(ValidationError):
        ReportParams(start_date='2026-10-01', end_date='2026-10-09', **{field:value})


def test_report_explicit_zero_and_large_integer_stop_conditions():
    base={'start_date':'2026-10-01','end_date':'2026-10-09'}
    empty=ReportParams(**base, harness_max_model_calls=0, harness_max_tool_calls=0, time_budget_seconds=0)
    assert (empty.harness_max_model_calls,empty.harness_max_tool_calls,empty.time_budget_seconds)==(0,0,0)
    large=ReportParams(**base,harness_max_model_calls=4096,harness_max_tool_calls=65536,time_budget_seconds=2592000)
    assert large.time_budget_seconds==2592000
