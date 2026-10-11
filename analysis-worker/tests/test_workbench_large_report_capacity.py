"""Large reports retain every source instead of enforcing a small item quota."""
import asyncio
import json

import httpx
import pytest

from app.workbench import pipeline


@pytest.mark.parametrize("count,body_size", [(201, 0), (1, 600000)])
def test_large_report_keeps_every_source_and_all_saved_text(monkeypatch, count, body_size):
    original_client = httpx.AsyncClient
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        calls.append((payload, data))
        if "candidate_report" in data:
            assert data["source_reviews"]
            content = json.dumps({"accepted": True, "issues": []})
        elif payload.get("response_format"):
            result = {"source_reviews": [{"citation_id": row["citation_id"],
                "quote": row["content_fragments"][0]["text"][:60],
                "reading": "来源自称讨论公开行动，尚需独立核验。", "limitations": "无法证明主体或执行情况。"}
                for row in data["sources"]]}
            content = json.dumps(result, ensure_ascii=False)
        else:
            content = "# 大范围分析\n\n来源称讨论公开行动，执行情况不足以判断。[S001]"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kwargs:
        original_client(transport=httpx.MockTransport(handler), **kwargs))
    items = [{"citation_id": f"S{index:03d}", "use_in_conclusions": True,
        "title": f"公開声明 {index}", "content": f"公開声明 {index}：集会を計画したとの自己申告です。" + "Saved public source text. " * (body_size // 26),
        "url": f"https://example.org/statements/{index}", "source_name": "公開資料",
        "type": "news", "published_at": "2026-10-08T00:00:00Z", "publication_day": "2026-10-08",
        "metadata": {"content_kind": "body"}} for index in range(1, count + 1)]
    snapshot = {"items": items, "scope": {"start_date": "2026-10-01", "end_date": "2026-10-09"}}
    markdown = asyncio.run(pipeline._ai_report(snapshot, "大范围分析"))
    assert "大范围分析" in markdown
    assert snapshot["provider_input"]["source_count"] == count
    assert snapshot["provider_input"]["omitted_source_count"] == 0
    assert snapshot["analysis_review"]["reading_state"]["complete"]
    assert {row["citation_id"] for row in snapshot["analysis_review"]["source_reviews"]} == {row["citation_id"] for row in items}
    assert len([payload for payload, _ in calls if not payload.get("response_format")]) == 1
    assert len([data for _, data in calls if "candidate_report" in data]) == 1
    assert snapshot["report_quality_review"]["requested"] is True
    assert all(row["complete_text_sent"] and not row["omitted_ranges"] for row in snapshot["analysis_review"]["coverage"])
    if body_size:
        assert snapshot["provider_input"]["wire_json_characters"] > 250000
        assert snapshot["provider_input"]["capacity"]["fits"]
        assert sum(row["sent_characters"] for row in snapshot["analysis_review"]["coverage"]) == sum(len(row["content"]) for row in items)
