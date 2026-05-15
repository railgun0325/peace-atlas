from app.sentiment import classify_sentiment, sentiment_profile_from_counts, summarize_sentiments


def test_rule_sentiment_labels():
    assert classify_sentiment("支持這個國防預算")["label"] == "positive"
    assert classify_sentiment("這樣黑箱令人憤怒")["label"] == "anger"
    assert classify_sentiment("台海風險讓人擔心")["label"] == "anxiety"
    assert classify_sentiment("笑死又來了")["label"] == "sarcasm"
    assert classify_sentiment("目前沒有更多資訊")["label"] == "neutral"


def test_sentiment_summary_counts_narratives():
    rows = [
        classify_sentiment("支持國防預算"),
        classify_sentiment("台海風險令人擔心"),
        classify_sentiment("立法院預算黑箱"),
    ]
    summary = summarize_sentiments(rows)
    assert summary["sentiment_counts"]["positive"] == 1
    assert summary["sentiment_counts"]["anxiety"] == 1
    assert summary["overall_attitude"] in {"样本较少，暂呈正向/支持", "样本较少，暂呈焦虑/担忧", "样本较少，暂呈愤怒/不满"}
    assert any(item["tag"] == "国防预算" for item in summary["top_narrative_tags"])


def test_sentiment_profile_outputs_attitude_and_percentages():
    profile = sentiment_profile_from_counts({"anxiety": 8, "anger": 2, "neutral": 0})
    assert profile["attitude_label"] == "整体偏焦虑/担忧"
    assert profile["percentages"]["anxiety"] == 80.0
    assert profile["icon"]
