from app.report_writer import fallback_report


def test_fallback_report_contains_required_tables():
    structured = {
        "event_clusters": [
            {
                "representative_title": "国防部说明台海周边动态",
                "article_count": 2,
                "source_count": 2,
                "source_types": ["official_government", "news_agency"],
                "source_names": ["国防部", "中央社"],
                "dominant_topics": ["国防", "两岸"],
                "entities": ["国防部", "立法院"],
                "evidence_grade": "B",
                "cluster_method": "local_similarity",
                "first_seen": "2026-05-10T00:00:00+00:00",
                "official_sources": [{"source_name": "国防部", "title": "说明", "url": "https://example.com/official"}],
                "sources": [{"source_name": "国防部", "title": "说明", "url": "https://example.com/official"}],
            }
        ],
        "topic_changes": [
            {"topic_name": "国防", "current_count": 2, "past_weekly_avg": 1.0, "change_label": "升温", "basis": "测试依据"}
        ],
        "entity_changes": [
            {"entity_name": "国防部", "current_count": 2, "past_weekly_avg": 1.0, "change_label": "升温"}
        ],
        "source_list": [
            {"source_name": "国防部", "source_type": "official_government", "title": "说明", "url": "https://example.com/official"}
        ],
        "historical_comparison": {"political_temperature": "升温"},
        "media_divergence": [],
    }

    markdown = fallback_report("2026-05-05", "2026-05-11", structured, "test")

    assert "事件簇表" in markdown
    assert "主题升温/降温表" in markdown
    assert "实体出现频次表" in markdown
    assert "来源覆盖情况" in markdown
    assert "证据等级说明" in markdown
    assert "🛡️ **2026-05-10**" in markdown
    assert "图标事件时间线" in markdown
