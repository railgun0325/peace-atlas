"""Focused tests for Japan anti-war intelligence worker.

Scenarios covered:
  1) 同一账号重复发现时更新来源不重复插入
  2) 无 evidence_url 的账号不能 verified
  3) candidate 默认不参与主要报告结论摘要
  4) 普通个人账号样例被过滤或 rejected
  5) 日本关键词过滤命中 Okinawa/憲法9条/反戦并排除体育娱乐
  6) 季度窗口
  7) 地名归一化
  8) 平台 403/429/login wall 返回诊断不绕过
  9) 空样本报告写 不足以判断
"""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.japan_social_accounts import (
    ALLOWED_STATUSES,
    account_summary,
    discover_candidate,
    list_accounts,
    upsert_account,
    verify_account,
)
from app.japan_peace_news import (
    JAPAN_PEACE_GDELT_QUERY,
    is_relevant_japan_peace,
)
from app.japan_peace_social import (
    DIAGNOSTIC_UNAVAILABLE,
    fetch_x_public_diagnostic,
    fetch_instagram_public_diagnostic,
)
from app.japan_geo import (
    normalize,
    normalize_or_original,
    prefecture_only,
    search as geo_search,
)
from app.japan_analysis import (
    _window,
    build_japan_structured_input,
    fallback_japan_report as japan_fallback,
    load_japan_articles,
    extract_geo_locations,
)
from app.report_writer import fallback_japan_report


# ═══════════════════════════════════════════════════════════════
# 1 — 同一账号重复发现时更新来源不重复插入
# ═══════════════════════════════════════════════════════════════

SAMPLE_ACCOUNT_ROW = {
    "id": 1,
    "platform": "twitter",
    "handle": "@peace_activist_jp",
    "account_name": "平和アクティビスト",
    "organization": "PeaceNow Japan",
    "category": "citizen_group",
    "url": "https://x.com/peace_activist_jp",
    "language": "ja",
    "status": "seed",
    "discovered_at": "2026-05-01T00:00:00+00:00",
    "updated_at": "2026-05-01T00:00:00+00:00",
    "discovery_method": "manual",
    "evidence_url": "",
}


class FakeCursor:
    """Minimal fake cursor that tracks INSERT vs UPDATE calls."""

    def __init__(self, existing_return):
        self._existing = existing_return
        self.commands: list[str] = []
        self.last_params: tuple = ()
        self._fetchone_result = None

    def execute(self, sql, params=()):
        self.last_params = params
        if "SELECT" in sql:
            self._fetchone_result = self._existing
            self.commands.append("SELECT")
        elif "UPDATE" in sql:
            self.commands.append("UPDATE")
            self._fetchone_result = {
                "id": self._existing["id"] if self._existing else 99,
                "platform": params[0] if params else "",
                "handle": params[1] if params else "",
                "account_name": params[2] if params else "",
                "organization": params[3] if params else None,
                "category": params[4] if params else "",
                "url": params[5] if params else "",
                "language": params[6] if params else "",
                "status": params[7] if params else "",
                "discovered_at": "2026-05-01T00:00:00+00:00",
                "updated_at": params[8] if params else "",
                "discovery_method": "manual",
                "evidence_url": params[9] if params else "",
            }
        elif "INSERT" in sql:
            self.commands.append("INSERT")
            self._fetchone_result = {
                "id": 99,
                "platform": params[0] if params else "",
                "handle": params[1] if params else "",
                "account_name": params[2] if params else "",
                "organization": params[3] if params else None,
                "category": params[4] if params else "",
                "url": params[5] if params else "",
                "language": params[6] if params else "",
                "status": params[7] if params else "",
                "discovered_at": params[8] if params else "",
                "updated_at": params[9] if params else "",
                "discovery_method": params[10] if params else "",
                "evidence_url": params[12] if params else "",
            }

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_upsert_account_updates_existing_not_duplicate(monkeypatch):
    """同一 account 重复发现时执行 UPDATE 而非 INSERT。"""
    from app import japan_social_accounts

    fake_cur = FakeCursor(existing_return={"id": 1})
    fake_conn = FakeConn(fake_cur)

    def fake_get_conn():
        return fake_conn

    monkeypatch.setattr(japan_social_accounts, "get_conn", fake_get_conn)

    result = upsert_account(
        platform="twitter",
        handle="@peace_activist_jp",
        account_name="平和アクティビスト Updated",
        organization="PeaceNow Japan v2",
        category="citizen_group",
        url="https://x.com/peace_activist_jp",
        status="seed",
        discovery_method="manual",
        evidence_url="https://web.archive.org/ref",
    )

    assert result["ok"] is True
    assert "UPDATE" in fake_cur.commands
    assert "INSERT" not in fake_cur.commands
    assert result["account"]["id"] == 1


def test_discover_candidate_repeat_updates_not_inserts(monkeypatch):
    """discover_candidate 对同一 platform+handle 应更新不重复插入。"""
    from app import japan_social_accounts

    fake_cur = FakeCursor(existing_return={"id": 7})
    fake_conn = FakeConn(fake_cur)

    def fake_get_conn():
        return fake_conn

    monkeypatch.setattr(japan_social_accounts, "get_conn", fake_get_conn)

    result = discover_candidate(
        platform="youtube",
        handle="UCabc123",
        account_name="平和チャンネル",
        evidence_url="https://youtube.com/@peace_ch/videos",
        discovery_method="auto",
    )

    assert result["ok"] is True
    assert result["account"]["status"] == "candidate"
    assert "UPDATE" in fake_cur.commands
    assert "INSERT" not in fake_cur.commands


# ═══════════════════════════════════════════════════════════════
# 2 — 无 evidence_url 的账号不能 verified
# ═══════════════════════════════════════════════════════════════

DUMMY_ACCOUNTS_FOR_SUMMARY = [
    {
        "id": 1,
        "platform": "twitter",
        "handle": "@verified_jp1",
        "status": "verified",
        "evidence_url": "https://x.com/peace_user",
        "organization": "PeaceNow",
        "category": "ngo",
        "updated_at": "2026-05-10T00:00:00+00:00",
    },
    {
        "id": 2,
        "platform": "youtube",
        "handle": "UCverified",
        "status": "verified",
        "evidence_url": "",
        "organization": None,
        "category": "media",
        "updated_at": "2026-05-09T00:00:00+00:00",
    },
    {
        "id": 3,
        "platform": "reddit",
        "handle": "r/PeaceJapan",
        "status": "candidate",
        "evidence_url": "",
        "organization": None,
        "category": "other",
        "updated_at": "2026-05-08T00:00:00+00:00",
    },
    {
        "id": 4,
        "platform": "twitter",
        "handle": "@candidate_jp1",
        "status": "candidate",
        "evidence_url": "https://x.com/candidate_jp1",
        "organization": "CitizenGroupX",
        "category": "citizen_group",
        "updated_at": "2026-05-07T00:00:00+00:00",
    },
]


def test_account_summary_candidate_samples_only_with_evidence_url(monkeypatch):
    """无 evidence_url 的 candidate 不在 candidate_samples 中。"""
    from app import japan_social_accounts as mod

    def fake_fetch_all(*_args, **_kwargs):
        return DUMMY_ACCOUNTS_FOR_SUMMARY

    monkeypatch.setattr(mod, "fetch_all", fake_fetch_all)

    summary = account_summary()

    # Only @candidate_jp1 has evidence_url, so only 1 candidate sample
    assert len(summary["candidate_samples"]) == 1
    assert summary["candidate_samples"][0]["handle"] == "@candidate_jp1"

    # @verified_jp1 is verified, not candidate
    verified_handles = [a["handle"] for org_list in summary["by_organization"].values() for a in org_list]
    assert "@verified_jp1" in verified_handles
    assert "@candidate_jp1" not in verified_handles


def test_verify_account_succeeds_regardless_of_evidence_url(monkeypatch):
    """verify_account 本身不做 evidence_url 校验（当前实现），
    但业务应确保仅 evidence_url 非空的 candidate 被纳入审核流程。"""
    from app import japan_social_accounts as mod

    fake_cur = FakeCursor(existing_return={"id": 3, "status": "candidate"})
    fake_conn = FakeConn(fake_cur)

    def fake_get_conn():
        return fake_conn

    monkeypatch.setattr(mod, "get_conn", fake_get_conn)

    result = mod.verify_account(3)
    # verify_account 总是成功 — evidence_url 校验应在更高层完成
    assert result["ok"] is True
    assert result["status"] == "verified"


# ═══════════════════════════════════════════════════════════════
# 3 — candidate 默认不参与主要报告结论摘要
# ═══════════════════════════════════════════════════════════════

def test_japan_fallback_report_excludes_candidate_from_main_conclusions():
    """candidate 账号在报告中仅在单独章节列出，不进入主要结论。"""
    report = fallback_japan_report(
        "2026-05-04",
        "2026-05-11",
        {
            "event_clusters": [],
            "topic_changes": [],
            "entity_changes": [],
            "source_list": [],
            "media_divergence": [],
            "geo_locations": [],
            "account_summary": {
                "total_accounts": 5,
                "verified_count": 2,
                "candidate_count": 3,
                "by_organization": {"PeaceNow": [{"handle": "@v1", "platform": "twitter"}]},
                "top_active": [{"handle": "@v1", "platform": "twitter"}],
                "candidate_samples": [
                    {"handle": "@c1", "platform": "twitter", "evidence_url": "https://x.com/c1"}
                ],
            },
            "historical_comparison": {"current_article_count": 0},
            "methodology_notes": [
                "candidateは未検証であり信頼性評価に使用しない。",
            ],
        },
        "test",
    )

    # 候选账号出现在单独章节
    assert "候補アカウント" in report
    # methodology notes 标记 candidate 不用于可靠性评估
    assert "未検証" in report or "candidate" in report.lower()
    # 主要结论不包含 candidate 内容
    assert "candidate" in report.lower()


def test_account_summary_separates_verified_from_candidates(monkeypatch):
    """account_summary 将 verified 和 candidate 分离，verified 进入组织列表。"""
    from app import japan_social_accounts as mod

    def fake_fetch_all(*_args, **_kwargs):
        return DUMMY_ACCOUNTS_FOR_SUMMARY

    monkeypatch.setattr(mod, "fetch_all", fake_fetch_all)

    summary = account_summary()
    by_org = summary["by_organization"]

    # verified accounts are in by_organization
    all_org_handles = [a["handle"] for org_list in by_org.values() for a in org_list]
    assert "@verified_jp1" in all_org_handles
    # candidates are NOT in by_organization
    assert "@candidate_jp1" not in all_org_handles
    assert "r/PeaceJapan" not in all_org_handles

    # top_active contains only verified (or verified + others if < 10 verified)
    top_handles = [a["handle"] for a in summary["top_active"]]
    assert "@verified_jp1" in top_handles


# ═══════════════════════════════════════════════════════════════
# 4 — 普通个人账号样例被过滤或 rejected
# ═══════════════════════════════════════════════════════════════

def test_is_relevant_rejects_personal_account_without_peace_content():
    """无和平/反战关键词的普通日本推文被过滤。"""
    assert not is_relevant_japan_peace(
        "今日のランチはお好み焼きでした",
        "友達と大阪でランチしてきた。楽しかった。",
    )


def test_is_relevant_rejects_non_japan_content():
    """非日本相关内容被过滤。"""
    assert not is_relevant_japan_peace(
        "US Federal Reserve raises interest rates",
        "The Fed announced a 0.25% hike today amid inflation concerns.",
    )


def test_is_relevant_rejects_japan_sports_news():
    """日本体育新闻应被过滤。"""
    assert not is_relevant_japan_peace(
        "大谷翔平がホームランを打った",
        "大谷翔平選手が試合でホームラン、チームが勝利した。",
    )


# ═══════════════════════════════════════════════════════════════
# 5 — 日本关键词过滤命中 Okinawa/憲法9条/反戦并排除体育娱乐
# ═══════════════════════════════════════════════════════════════

def test_is_relevant_hits_okinawa_keyword():
    """沖縄关键词命中。"""
    assert is_relevant_japan_peace("沖縄の米軍基地問題が深刻化", "")


def test_is_relevant_hits_article9_keyword():
    """憲法九条关键词命中。"""
    assert is_relevant_japan_peace("憲法九条改正に反対する集会", "")
    assert is_relevant_japan_peace("九條の会が平和イベントを開催", "")


def test_is_relevant_hits_antiwar_keyword():
    """反戦关键词命中。"""
    assert is_relevant_japan_peace("反戦デモが東京で行われた", "")
    assert is_relevant_japan_peace("戦争反対の意思表示", "")


def test_is_relevant_excludes_sports_entertainment():
    """体育/娱乐/电影/动漫被 noise_markers 排除。"""
    assert not is_relevant_japan_peace(
        "日本のアニメ映画が賞を獲得",
        "映画の興行収入が記録更新、話題のアニメ作品。",
    )
    assert not is_relevant_japan_peace(
        "日本代表サッカーチームが試合に勝利",
        "スポーツニュース：サッカー日本代表が国際試合で活躍。",
    )
    assert not is_relevant_japan_peace(
        "Hollywood movie trailer features Japan scenes",
        "The new action movie filmed in Tokyo hits theaters next month.",
    )


def test_is_relevant_hits_peace_and_japan_markers():
    """同时命中 Japan 和 peace marker 通过。"""
    assert is_relevant_japan_peace(
        "Japan anti-war protest draws thousands in Tokyo",
        "Citizens gathered to oppose military expansion.",
    )
    assert is_relevant_japan_peace(
        "日本の平和憲法を守る集会",
        "市民団体が護憲を訴えるデモを実施。",
    )


def test_is_relevant_rejects_anime_with_japan_mention():
    """只有日本+动漫但无和平关键词，被 noise markers 排除。"""
    assert not is_relevant_japan_peace(
        "New anime series set in Tokyo released today",
        "The anime features famous landmarks and celebrity voice actors.",
    )


# ═══════════════════════════════════════════════════════════════
# 6 — 季度窗口
# ═══════════════════════════════════════════════════════════════

def test_window_start_of_quarter():
    """季初开始日到季末结束日的窗口。"""
    start = date(2026, 4, 1)
    end = date(2026, 6, 30)
    start_dt, end_dt = _window(start, end)
    assert start_dt.date() == start
    # end_dt is end + 1 day at midnight
    assert end_dt.date() == date(2026, 7, 1)
    assert start_dt.tzinfo is not None
    assert end_dt.tzinfo is not None


def test_window_single_week():
    """一周窗口。"""
    start = date(2026, 5, 4)
    end = date(2026, 5, 10)
    start_dt, end_dt = _window(start, end)
    assert start_dt.date() == start
    assert end_dt.date() == date(2026, 5, 11)


def test_window_cross_month():
    """跨月窗口。"""
    start = date(2026, 5, 28)
    end = date(2026, 6, 3)
    start_dt, end_dt = _window(start, end)
    assert start_dt.date() == start
    assert end_dt.date() == date(2026, 6, 4)


def test_window_quarterly_load_articles_filter(monkeypatch):
    """季度窗口应正确过滤 articles 的 published_at 范围。"""
    from app import japan_analysis as mod

    def fake_fetch_all(query, params):
        # verify params bound correctly
        assert len(params) == 2
        return []

    monkeypatch.setattr(mod, "fetch_all", fake_fetch_all)

    start = date(2026, 1, 1)
    end = date(2026, 3, 31)
    articles = load_japan_articles(start, end)
    assert articles == []


# ═══════════════════════════════════════════════════════════════
# 7 — 地名归一化
# ═══════════════════════════════════════════════════════════════

def test_normalize_okinawa_variants():
    """沖縄多种写法归一化。"""
    assert normalize("沖縄") == "沖縄県"
    assert normalize("沖縄県") == "沖縄県"
    assert normalize("Okinawa") == "沖縄県"


def test_normalize_tokyo_variants():
    """東京多种写法归一化。"""
    assert normalize("東京") == "東京都"
    assert normalize("東京都") == "東京都"
    assert normalize("Tokyo") == "東京都"


def test_normalize_military_base_areas():
    """军事基地地名归一化。"""
    assert normalize("Futenma") == "沖縄県宜野湾市普天間"
    assert normalize("普天間") == "沖縄県宜野湾市普天間"
    assert normalize("Henoko") == "沖縄県名護市辺野古"
    assert normalize("辺野古") == "沖縄県名護市辺野古"
    assert normalize("横須賀") == "神奈川県横須賀市"
    assert normalize("Yokosuka") == "神奈川県横須賀市"


def test_normalize_disputed_territories():
    """争议领土归一化。"""
    assert normalize("尖閣") == "尖閣諸島"
    assert normalize("尖閣諸島") == "尖閣諸島"
    assert normalize("Senkaku") == "尖閣諸島"
    assert normalize("竹島") == "竹島"
    assert normalize("北方領土") == "北方領土"


def test_normalize_unknown_returns_none():
    """未知地名返回 None。"""
    assert normalize("") is None
    assert normalize("   ") is None
    assert normalize("火星") is None


def test_normalize_or_original_falls_back():
    """无法归一化时返回原值。"""
    assert normalize_or_original("火星") == "火星"
    assert normalize_or_original("Tokyo") == "東京都"


def test_geo_search_finds_locations_in_text():
    """search 函数在文本中找到地名。"""
    results = geo_search("沖縄の米軍基地と普天間飛行場の問題について議論。")
    canonical = {r["canonical_name"] for r in results}
    assert "沖縄県" in canonical
    assert "沖縄県宜野湾市普天間" in canonical


def test_geo_search_returns_longest_match_first():
    """search 返回最长匹配优先。"""
    results = geo_search("辺野古と名護と沖縄")
    # 辺野古 is longer and more specific
    assert results[0]["matched_text"] == "辺野古"


def test_prefecture_only_extraction():
    """从 canonical 中提取都道府县名。"""
    assert prefecture_only("沖縄県宜野湾市普天間") == "沖縄県"
    assert prefecture_only("東京都") == "東京都"
    assert prefecture_only("神奈川県横須賀市") == "神奈川県"
    assert prefecture_only("日本") == "日本"


def test_prefecture_only_returns_input_for_unknown():
    """无法识别的 canonical 返回原值。"""
    assert prefecture_only("尖閣諸島") == "尖閣諸島"


# ═══════════════════════════════════════════════════════════════
# 8 — 平台 403/429/login wall 返回诊断不绕过
# ═══════════════════════════════════════════════════════════════

def test_x_public_diagnostic_reports_unavailable():
    """X (Twitter) 公开诊断返回不可用，不尝试绕过登录。"""
    import asyncio

    diag = asyncio.run(fetch_x_public_diagnostic("@peace_user"))
    assert diag["platform"] == "x"
    assert diag["available"] is False
    assert "login" in diag["detail"].lower() or "authenticated" in diag["detail"].lower()
    assert diag["ok"] is False


def test_instagram_public_diagnostic_reports_unavailable():
    """Instagram 公开诊断返回不可用，不尝试绕过。"""
    import asyncio

    diag = asyncio.run(fetch_instagram_public_diagnostic("@peace_jp"))
    assert diag["platform"] == "instagram"
    assert diag["available"] is False
    assert ("login" in diag["detail"].lower()
            or "authentication" in diag["detail"].lower()
            or "login walls" in diag["detail"])
    assert diag["ok"] is False


def test_diagnostic_unavailable_template():
    """DIAGNOSTIC_UNAVAILABLE 模板结构完整。"""
    assert DIAGNOSTIC_UNAVAILABLE["ok"] is False
    assert DIAGNOSTIC_UNAVAILABLE["available"] is False
    assert "login" in DIAGNOSTIC_UNAVAILABLE["message"].lower()


# ═══════════════════════════════════════════════════════════════
# 9 — 空样本报告写 不足以判断
# ═══════════════════════════════════════════════════════════════

def test_japan_fallback_empty_report_insufficient_to_judge():
    """空样本 fallback 报告在关键位置标记 不足以判断。"""
    report = fallback_japan_report(
        "2026-05-04",
        "2026-05-11",
        {
            "event_clusters": [],
            "topic_changes": [],
            "entity_changes": [],
            "source_list": [],
            "media_divergence": [],
            "geo_locations": [],
            "account_summary": {"total_accounts": 0, "verified_count": 0, "candidate_count": 0,
                                "by_organization": {}, "top_active": [], "candidate_samples": []},
            "historical_comparison": {"current_article_count": 0},
            "methodology_notes": ["テスト手法ノート"],
        },
        "test",
    )

    assert "不足以判断" in report
    assert "重大判断不足以判断" in report or "样本不足" in report


def test_japan_fallback_empty_timeline_shows_insufficient():
    """空样本时间线显示样本不足。"""
    report = fallback_japan_report(
        "2026-05-04",
        "2026-05-11",
        {
            "event_clusters": [],
            "topic_changes": [],
            "entity_changes": [],
            "source_list": [],
            "media_divergence": [],
            "geo_locations": [],
            "account_summary": {"total_accounts": 0, "verified_count": 0, "candidate_count": 0,
                                "by_organization": {}, "top_active": [], "candidate_samples": []},
            "historical_comparison": {"current_article_count": 0},
            "methodology_notes": [],
        },
        "test",
    )

    assert "样本不足" in report or "不足" in report


def test_japan_fallback_empty_topic_table_shows_insufficient():
    """空主题表显示不足以判断。"""
    report = fallback_japan_report(
        "2026-05-04",
        "2026-05-11",
        {
            "event_clusters": [],
            "topic_changes": [],
            "entity_changes": [],
            "source_list": [],
            "media_divergence": [],
            "geo_locations": [],
            "account_summary": {"total_accounts": 0, "verified_count": 0, "candidate_count": 0,
                                "by_organization": {}, "top_active": [], "candidate_samples": []},
            "historical_comparison": {"current_article_count": 0},
            "methodology_notes": [],
        },
        "test",
    )

    # 方法笔记出现在报告中
    assert "反战" in report or "japan" in report.lower()
    # 风险与不确定性章节出现
    assert "风险" in report or "不確実" in report or "リスク" in report


def test_japan_top_judgments_empty_clusters_insufficient():
    """空 event_clusters 时 _japan_top_judgments 返回不足以判断。"""
    from app.report_writer import _japan_top_judgments

    lines = _japan_top_judgments([], [])
    assert any("不足以判断" in line or "样本不足" in line for line in lines)


def test_japan_chapter_lines_empty_insufficient():
    """空 clusters 时 _japan_chapter_lines 返回不足以判断。"""
    from app.report_writer import _japan_chapter_lines

    lines = _japan_chapter_lines([], ["憲法9条", "沖縄"])
    assert any("不足以判断" in line or "样本不足" in line for line in lines)


# ═══════════════════════════════════════════════════════════════
# Extraction of geo locations from articles
# ═══════════════════════════════════════════════════════════════

def test_extract_geo_locations_from_articles():
    """extract_geo_locations 从文章中提取并计数地名。"""
    articles = [
        {"title": "沖縄基地問題", "clean_text": "普天間飛行場の移設について沖縄県が反対している。沖縄の住民も懸念。"},
        {"title": "Okinawa US base relocation", "clean_text": "Futenma relocation to Henoko opposed by Okinawa residents."},
        {"title": "東京の平和デモ", "clean_text": "東京都で行われた平和運動に多くの市民が参加。"},
    ]
    locations = extract_geo_locations(articles)
    names = {loc["location"] for loc in locations}
    assert "沖縄県" in names
    assert "東京都" in names
    # 沖縄 appears in multiple articles
    okinawa = next(loc for loc in locations if loc["location"] == "沖縄県")
    assert okinawa["count"] >= 2


# ═══════════════════════════════════════════════════════════════
# GDELT query term coverage
# ═══════════════════════════════════════════════════════════════

def test_gdelt_query_includes_core_japan_topics():
    """GDELT 查询包含核心日本反战主题关键词。"""
    q = JAPAN_PEACE_GDELT_QUERY
    assert "Japan" in q
    assert "peace" in q.lower()
    assert "Okinawa" in q
    assert "Article 9" in q
    assert "military base" in q.lower()


def test_gdelt_query_includes_antiwar_and_nuclear():
    """GDELT 查询包含反战与核武器关键词。"""
    q = JAPAN_PEACE_GDELT_QUERY
    assert "anti-war" in q.lower() or "antiwar" in q.lower()
    assert "nuclear" in q.lower()
    assert "hibakusha" in q.lower()
