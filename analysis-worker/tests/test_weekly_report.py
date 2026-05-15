from app.report_writer import fallback_report


def test_fallback_report_contains_public_discussion_section():
    markdown = fallback_report(
        "2026-05-04",
        "2026-05-11",
        {
            "event_clusters": [],
            "topic_changes": [],
            "entity_changes": [],
            "source_list": [],
            "media_divergence": [],
            "historical_comparison": {"political_temperature": "不足以判断"},
            "discussion_signals": {
                "window": {"start": "2026-05-04", "end": "2026-05-11"},
                "post_count": 1,
                "comment_count": 2,
                "platform_counts": {"ptt": 1},
                "sentiment_counts": {"anxiety": 1},
                "sentiment_percentages": {"anxiety": 100.0},
                "recent_sentiment": {
                    "window": {"start": "2026-05-04", "end": "2026-05-11"},
                    "post_count": 1,
                    "comment_count": 2,
                    "sentiment_sample_size": 1,
                    "sentiment_counts": {"anxiety": 1},
                    "sentiment_percentages": {"anxiety": 100.0},
                    "overall_attitude": "样本较少，暂呈焦虑/担忧",
                    "overall_attitude_icon": "⚠️",
                    "overall_attitude_color": "#f59e0b",
                },
                "long_term_sentiment": {
                    "window": {"start": "2026-03-17", "end": "2026-05-11"},
                    "post_count": 8,
                    "comment_count": 30,
                    "sentiment_sample_size": 20,
                    "sentiment_counts": {"neutral": 12, "anxiety": 8},
                    "sentiment_percentages": {"neutral": 60.0, "anxiety": 40.0},
                    "overall_attitude": "整体偏中性，缺少明确情绪方向",
                    "overall_attitude_icon": "●",
                    "overall_attitude_color": "#64748b",
                },
                "sentiment_comparison": {"label": "近期情绪方向变化", "basis": "测试依据"},
                "narrative_counts": {"两岸安全": 1},
                "top_posts": [
                    {
                        "platform": "ptt",
                        "community": "HatePolitics",
                        "title": "台海討論",
                        "comment_count": 2,
                        "url": "https://www.ptt.cc/bbs/HatePolitics/M.1.html",
                    }
                ],
            },
        },
        "test",
    )
    assert "公众讨论与情绪信号" in markdown
    assert "不能作为事实判断依据" in markdown
    assert "样本量：1 篇帖子、2 条评论" in markdown
    assert "近期总体态度" in markdown
    assert "长期总体态度" in markdown
    assert "<!-- jai-visual-signals-start -->" in markdown
