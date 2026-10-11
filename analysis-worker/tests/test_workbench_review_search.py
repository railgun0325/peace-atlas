"""Public-account discovery quality uses controlled responses, never live I/O.

Search locators remain unverified; final-page reading and owner-backlink checks
belong to the review gateway and the independent account-verification lane.
"""
import asyncio
import copy
from html import escape
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from app.workbench import review_search as search


PUBLIC_SOURCE = "https://union.example.org/about"


def account(*, name="Doro", handle="doro_chiba", platform="twitter", **values):
    host = "x.com" if platform == "twitter" else "www.instagram.com"
    return {
        "id": "public-fixture-account",
        "platform": platform,
        "handle": handle,
        "name": name,
        "url": f"https://{host}/{handle}",
        "status": "candidate",
        "metadata": {},
        **values,
    }


def result(url=PUBLIC_SOURCE, title="Public reference", description=""):
    return {"url": url, "title": title, "description": description}


def rss(request_url, items=(), *, echo_url=None):
    entries = "".join(
        "<item>"
        f"<title>{escape(row['title'])}</title>"
        f"<link>{escape(row['url'])}</link>"
        f"<description>{escape(row['description'])}</description>"
        "</item>"
        for row in items
    )
    return (
        '<rss version="2.0"><channel><title>Public search</title>'
        f"<link>{escape(echo_url if echo_url is not None else request_url)}</link>"
        f"{entries}</channel></rss>"
    ).encode("utf-8")


def ddg(items=()):
    entries = "".join(
        '<div class="result">'
        f'<a class="result__a" href="{escape(row["url"], quote=True)}">'
        f'{escape(row["title"])}</a>'
        f'<div class="result__snippet">{escape(row["description"])}</div>'
        "</div>"
        for row in items
    )
    return f'<html><body><div id="links" class="results">{entries}</div></body></html>'.encode("utf-8")


def google(items=()):
    entries = "".join(
        '<div class="public-result">'
        f'<a href="{escape(row["url"], quote=True)}"><h3>{escape(row["title"])}</h3></a>'
        f'<div class="snippet">{escape(row["description"])}</div>'
        "</div>"
        for row in items
    )
    return f'<html><body><div id="search">{entries}</div></body></html>'.encode("utf-8")


def provider(url):
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("bing.com"):
        return "bing"
    if host.endswith("duckduckgo.com"):
        return "ddg"
    if host.endswith("google.com"):
        return "google"
    raise AssertionError(f"Unexpected fixture provider: {host}")


@pytest.fixture(autouse=True)
def no_live_public_transport(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("This regression must not use real public transport.")

    monkeypatch.setattr(search.pipeline, "_fetch_public_feed", forbidden)


def responses(monkeypatch, *, bing=(), duck=(), goog=(), overrides=None):
    """Capture the actual request; providers not supplied return honest empties."""
    calls = []
    overrides = overrides or {}

    async def fetch(url, **kwargs):
        calls.append((url, kwargs))
        stage = provider(url)
        if stage in overrides:
            value = overrides[stage]
            if isinstance(value, BaseException):
                raise value
            return value(url)
        body = rss(url, bing) if stage == "bing" else ddg(duck) if stage == "ddg" else google(goog)
        return body, url, 200

    monkeypatch.setattr(search.pipeline, "_fetch_public_feed", fetch)
    return calls


def run(value=None):
    return asyncio.run(search.search_account_public(value or account()))


def assert_candidate(row):
    assert set(("url", "title", "description", "match_basis", "requires_owner_backlink")) <= set(row)
    assert isinstance(row["match_basis"], list) and row["match_basis"]
    assert all(isinstance(basis, str) and basis for basis in row["match_basis"])
    assert row["requires_owner_backlink"] is True
    assert "verified" not in row and "account_type" not in row


def assert_diagnostics(value):
    assert value["search_is_not_verification"] is True
    assert isinstance(value["saved_sources"], list)
    assert isinstance(value["search_results"], list)
    assert isinstance(value["diagnostics"], list)
    for stage in value["diagnostics"]:
        assert {"provider", "query", "status", "raw_count", "relevant_count", "rejected_count", "rejected", "rejected_reason_counts", "fallback_reason"} <= set(stage)
        counts = [stage[key] for key in ("raw_count", "relevant_count", "rejected_count")]
        assert all(type(count) is int and 0 <= count <= 10 for count in counts)
        assert stage["raw_count"] == stage["relevant_count"] + stage["rejected_count"]
        assert len(stage["rejected"]) == stage["rejected_count"]
        assert all(set(row) == {"reason"} and isinstance(row["reason"], str) and 0 < len(row["reason"]) <= 100 for row in stage["rejected"])
        reason_counts = stage["rejected_reason_counts"]
        assert isinstance(reason_counts, dict)
        assert sum(reason_counts.values()) == stage["rejected_count"]
        assert all(isinstance(reason, str) and 0 < len(reason) <= 100 and type(count) is int and count > 0
                   for reason, count in reason_counts.items())
        assert all(reason_counts[reason] == sum(row["reason"] == reason for row in stage["rejected"])
                   for reason in reason_counts)
    for row in value["search_results"]:
        assert_candidate(row)


def test_unrelated_singer_dictionary_and_microsoft_results_do_not_count_as_account_locators(monkeypatch):
    unrelated = [
        result("https://music.example.org/doro", "Doro singer", "Doro albums and biography"),
        result("https://dictionary.example.org/doro", "Doro dictionary", "Definition of doro"),
        result("https://microsoft.example.org/help", "Microsoft help", "Public account help"),
    ]
    exact = result(description="Official Twitter account @DORO_CHIBA")
    calls = responses(monkeypatch, bing=[*unrelated, exact])
    value = run()
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert len(calls) == 1
    first = value["diagnostics"][0]
    assert (first["raw_count"], first["relevant_count"], first["rejected_count"]) == (4, 1, 3)
    assert all(set(row) == {"reason"} for row in first["rejected"])
    assert_diagnostics(value)


@pytest.mark.parametrize("near", ["doro_chiba2", "not_doro_chiba", "doro_chiba_news", "doro_chiba.foo"])
def test_near_handle_does_not_match_exact_target_even_with_platform_context(monkeypatch, near):
    calls = responses(monkeypatch, bing=[result(description=f"Twitter official account @{near}")])
    value = run()
    assert value["search_results"] == []
    assert len(calls) == 3
    assert value["diagnostics"][0]["relevant_count"] == 0
    assert value["diagnostics"][0]["rejected_count"] == 1
    assert_diagnostics(value)


@pytest.mark.parametrize("description", [
    "Twitter account @DORO_CHIBA", "X account: doro_chiba", "公式アカウント doro_chiba 動労千葉",
])
def test_exact_handle_has_platform_or_distinct_target_context(monkeypatch, description):
    calls = responses(monkeypatch, bing=[result(description=description)])
    value = run(account(name="動労千葉"))
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert len(calls) == 1
    assert_diagnostics(value)


def test_bare_exact_handle_without_platform_or_target_context_is_ambiguous(monkeypatch):
    responses(monkeypatch, bing=[result(title="Word directory", description="doro_chiba")])
    value = run()
    assert value["search_results"] == []
    assert value["diagnostics"][0]["relevant_count"] == 0
    assert_diagnostics(value)


@pytest.mark.parametrize("homepage", [
    "https://x.com/DORO_CHIBA/", "https://twitter.com/doro_chiba?ref=home", "https://www.twitter.com:443/doro_chiba/",
])
def test_accurate_platform_homepage_is_a_strong_locator(monkeypatch, homepage):
    calls = responses(monkeypatch, bing=[result(description=f"Published account: {homepage}")])
    value = run()
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert len(calls) == 1
    assert_diagnostics(value)


@pytest.mark.parametrize("wrong", [
    "https://www.instagram.com/doro_chiba/", "https://x.com/doro_chiba_other/",
    "https://x.com/doro_chiba/status/123", "https://x.com/intent/follow?screen_name=doro_chiba",
    "https://x.com.evil.example.org/doro_chiba", "http://x.com/doro_chiba",
])
def test_wrong_platform_near_or_non_homepage_url_does_not_gain_exact_url_match(monkeypatch, wrong):
    responses(monkeypatch, bing=[result(title="Published reference", description=wrong)])
    value = run()
    assert value["search_results"] == []
    assert_diagnostics(value)


def test_wrong_platform_homepage_cannot_be_rescued_by_an_unrelated_twitter_word(monkeypatch):
    responses(monkeypatch, bing=[result(title="Twitter references", description="https://www.instagram.com/doro_chiba/")])
    assert run()["search_results"] == []


@pytest.mark.parametrize("name", ["Doro", "Public A"])
def test_ambiguous_display_name_alone_cannot_create_a_related_candidate(monkeypatch, name):
    responses(monkeypatch, bing=[result(title=f"{name} official website", description=f"Information about {name}")])
    value = run(account(name=name))
    assert value["search_results"] == []
    assert value["diagnostics"][0]["rejected_count"] == 1
    assert_diagnostics(value)


@pytest.mark.parametrize("name,title,url", [
    ("動労千葉", "動労千葉の組織概要", PUBLIC_SOURCE),
    ("全学連", "全学連について", PUBLIC_SOURCE),
    ("Peace Workers", "Peace Workers official organization", PUBLIC_SOURCE),
    ("doro-chiba", "Organization overview", "https://doro-chiba.org/about"),
])
def test_distinct_complete_display_name_can_locate_a_page_without_granting_identity(monkeypatch, name, title, url):
    calls = responses(monkeypatch, bing=[result(url, title)])
    value = run(account(name=name))
    assert [row["url"] for row in value["search_results"]] == [url]
    assert len(calls) == 1
    assert_diagnostics(value)


def test_public_display_name_newline_is_folded_without_weakening_handle_or_homepage_validation(monkeypatch):
    target = account(name="全国大学生協連\n学生委員会", handle="uc_zengakui")
    original = copy.deepcopy(target)
    calls = responses(monkeypatch, bing=[result(title="全国大学生協連 学生委員会の紹介")])
    value = run(target)
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert len(calls) == 1
    assert target == original
    assert all("\n" not in query for url, _ in calls for values in parse_qs(urlsplit(url).query).values() for query in values)
    assert_diagnostics(value)


@pytest.mark.parametrize("final_host", ["www.google.com", "www.google.com.hk", "www.google.co.jp"])
def test_google_public_regional_redirect_keeps_the_same_bounded_provider(monkeypatch, final_host):
    exact = result(description="Official Twitter @doro_chiba")
    calls = responses(monkeypatch, overrides={
        "google": lambda url: (google([exact]), url.replace("www.google.com", final_host), 200),
    })
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg", "google"]
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert_diagnostics(value)


def test_google_200_javascript_shell_is_a_limited_provider_without_candidates(monkeypatch):
    shell = b"""<html><head><title>Google Search</title></head><body>
    <a href="/httpservice/retry/enablejs?sei=public-fixture">Enable JavaScript</a>
    <a href="/httpservice/retry/enablejs?retry=1">Try again</a>
    <a href="https://support.google.com/websearch">Search support</a>
    </body></html>"""
    calls = responses(monkeypatch, overrides={
        "google": lambda url: (shell, url.replace("www.google.com", "www.google.com.hk"), 200),
    })
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg", "google"]
    assert value["search_results"] == []
    last = value["diagnostics"][-1]
    assert last["provider"] == "google" and last["status_code"] == 200
    assert last["status"] == "limited"
    assert last["fallback_reason"] == "provider_requires_javascript"
    assert last["raw_count"] == last["relevant_count"] == last["rejected_count"] == 0
    assert value["discovery_is_limited"] is True
    assert_diagnostics(value)


@pytest.mark.parametrize("source", [
    "https://127.0.0.1/about", "https://10.0.0.1/about", "https://172.16.0.1/about",
    "https://192.168.1.1/about", "https://198.18.0.70/about", "https://[::1]/about",
    "https://localhost/about", "https://host.localhost/about", "https://workstation.local/about",
])
def test_private_or_local_source_is_rejected_even_with_an_exact_account_locator(monkeypatch, source):
    calls = responses(monkeypatch, bing=[result(source, description="Official Twitter @doro_chiba")])
    value = run()
    assert len(calls) == 3
    assert value["search_results"] == []
    assert value["diagnostics"][0]["rejected_count"] == 1
    assert value["diagnostics"][0]["relevant_count"] == 0
    assert_diagnostics(value)


@pytest.mark.parametrize("source", ["https://127.0.0.1/about", "https://localhost/about"])
def test_source_registration_does_not_exempt_a_private_or_local_candidate(monkeypatch, source):
    calls = responses(monkeypatch)
    target = account(metadata={"verification_sources": [{"url": source, "trust_basis": "manual_registry"}]})
    value = run(target)
    assert len(calls) == 3
    assert value["saved_sources"] == [] and value["search_results"] == []
    assert_diagnostics(value)


def test_search_wrapper_query_and_query_echo_do_not_supply_account_relevance(monkeypatch):
    wrappers = [
        result("https://www.bing.com/search?q=doro_chiba+Twitter", "Doro dictionary", "Definition of doro"),
        result("https://www.google.com/search?q=https%3A%2F%2Fx.com%2Fdoro_chiba", "Doro singer", "Albums"),
        result("https://duckduckgo.com/?q=doro_chiba+Twitter", "Public search", "Music"),
    ]
    responses(monkeypatch, bing=wrappers)
    value = run()
    assert value["search_results"] == []
    assert value["diagnostics"][0]["rejected_count"] == 3
    assert all(set(row) == {"reason"} for row in value["diagnostics"][0]["rejected"])
    assert_diagnostics(value)


def test_nonempty_raw_results_with_zero_relevant_locators_trigger_next_provider(monkeypatch):
    bad = [result(f"https://music.example.org/{index}", "Doro singer", "Albums") for index in range(10)]
    exact = result(description="Official Twitter @doro_chiba")
    calls = responses(monkeypatch, bing=bad, duck=[exact])
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg"]
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    first = value["diagnostics"][0]
    assert (first["raw_count"], first["relevant_count"], first["rejected_count"]) == (10, 0, 10)
    assert first["fallback_reason"]
    assert_diagnostics(value)


@pytest.mark.parametrize("failure", ["echo_mismatch", "wrong_schema", "captcha", "malformed_xml"])
def test_bing_response_must_validate_query_echo_and_rss_schema_before_accepting_results(monkeypatch, failure):
    exact = result(description="Official Twitter @doro_chiba")

    def broken(url):
        if failure == "echo_mismatch":
            return rss(url, [exact], echo_url="https://www.bing.com/search?q=unrelated&format=rss"), url, 200
        if failure == "wrong_schema":
            return b"<results><item><title>Twitter @doro_chiba</title></item></results>", url, 200
        if failure == "captcha":
            return b"<html><title>Verify you are a human</title><div>CAPTCHA</div></html>", url, 200
        return b"<rss><channel><item>", url, 200

    calls = responses(monkeypatch, duck=[exact], overrides={"bing": broken})
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg"]
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert value["diagnostics"][0]["relevant_count"] == 0
    assert value["diagnostics"][0]["fallback_reason"]
    assert_diagnostics(value)


def test_ddg_captcha_advances_to_google_and_unwraps_actual_destination(monkeypatch):
    destination = "/url?q=" + quote(PUBLIC_SOURCE, safe="") + "&sa=U"
    exact = result(destination, "Official Twitter account", "@doro_chiba")
    calls = responses(monkeypatch, goog=[exact], overrides={
        "ddg": lambda url: (b"<html><title>DuckDuckGo</title><form id='challenge-form'>CAPTCHA verify human</form></html>", url, 200),
    })
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg", "google"]
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert value["diagnostics"][1]["fallback_reason"]
    assert_diagnostics(value)


@pytest.mark.parametrize("final_url", [
    "https://evil.example.org/search", "https://www.google.com/search?q=doro_chiba",
    "http://www.bing.com/search?q=doro_chiba", "https://www.bing.com:8443/search?q=doro_chiba",
])
def test_provider_redirect_to_an_unexpected_authority_cannot_pass_a_relevant_response(monkeypatch, final_url):
    bad = result("https://bad.example.org/about", description="Official Twitter @doro_chiba")
    good = result(description="Official Twitter @doro_chiba")
    calls = responses(monkeypatch, duck=[good], overrides={"bing": lambda url: (rss(url, [bad]), final_url, 200)})
    value = run()
    assert [provider(url) for url, _ in calls] == ["bing", "ddg"]
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert value["diagnostics"][0]["fallback_reason"]
    assert_diagnostics(value)


def test_all_providers_empty_are_honest_bounded_discovery_without_fake_sources(monkeypatch):
    original = account()
    before = copy.deepcopy(original)
    calls = responses(monkeypatch)
    value = run(original)
    assert [provider(url) for url, _ in calls] == ["bing", "ddg", "google"]
    assert value["saved_sources"] == [] and value["search_results"] == []
    assert all(stage["raw_count"] == stage["relevant_count"] == stage["rejected_count"] == 0 for stage in value["diagnostics"])
    assert original == before
    assert "verified" not in value and "account_type" not in value
    assert_diagnostics(value)


def test_transport_failures_exhaust_at_most_three_requests_and_preserve_safe_reasons(monkeypatch):
    marker = "fixture-secret-must-not-be-disclosed"
    calls = responses(monkeypatch, overrides={
        "bing": OSError(marker), "ddg": asyncio.TimeoutError(marker), "google": OSError(marker),
    })
    value = run()
    assert len(calls) == 3
    assert value["search_results"] == []
    assert value.get("search_unavailable") is True
    assert all(stage["fallback_reason"] for stage in value["diagnostics"])
    assert marker not in str(value)
    assert_diagnostics(value)


def test_each_provider_parses_at_most_ten_raw_items_and_can_fallback_when_the_eleventh_is_relevant(monkeypatch):
    unrelated = [result(f"https://music.example.org/{index}", "Doro singer", "Albums") for index in range(10)]
    ignored_eleventh = result("https://eleventh.example.org/about", description="Official Twitter @doro_chiba")
    exact = result(description="Official Twitter @doro_chiba")
    calls = responses(monkeypatch, bing=[*unrelated, ignored_eleventh], duck=[exact])
    value = run()
    assert len(calls) == 2
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert value["diagnostics"][0]["raw_count"] == 10
    assert value["diagnostics"][0]["relevant_count"] == 0
    assert_diagnostics(value)


def test_duplicate_locators_do_not_repeat_the_same_destination_or_discard_unique_candidates(monkeypatch):
    first = result(description="Official Twitter @doro_chiba")
    second = result("https://second.example.org/about", description="Official Twitter @doro_chiba")
    calls = responses(monkeypatch, bing=[first, first, second, second])
    value = run()
    assert {row["url"] for row in value["search_results"]} == {PUBLIC_SOURCE, second["url"]}
    assert len(value["search_results"]) == 2 and len(calls) == 1
    assert_diagnostics(value)


def test_registered_saved_sources_are_prioritized_and_deduplicated_during_bounded_supplemental_search(monkeypatch):
    registered = "https://registered.example.org/about"
    other = "https://registered.example.org/contact"
    value_account = account(metadata={"verification_sources": [
        {"url": registered, "trust_basis": "manual_registry", "account_type": "organization"},
        {"url": registered, "trust_basis": "manual_registry", "account_type": "organization"},
        {"url": other, "trust_basis": "evidence_review_registry", "account_type": "unknown"},
    ]})
    before = copy.deepcopy(value_account)
    calls = responses(monkeypatch, bing=[result(description="Official Twitter @doro_chiba")])
    value = run(value_account)
    assert len(calls) == 1
    assert {row["url"] for row in value["saved_sources"]} == {registered, other}
    assert len(value["saved_sources"]) == 2
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert value_account == before
    assert "verified" not in value
    assert_diagnostics(value)


def test_supplemental_search_failures_do_not_hide_registered_saved_sources_or_grant_verification(monkeypatch):
    registered = "https://registered.example.org/about"
    profile_site = "https://unregistered.example.org/"
    value_account = account(metadata={
        "verification_sources": [{"url": registered, "trust_basis": "manual_registry", "account_type": "organization"}],
        "last_public_profile": {"links": [{"url": profile_site}]},
    })
    before = copy.deepcopy(value_account)
    calls = responses(monkeypatch, overrides={
        "bing": OSError("fixture transport failed"), "ddg": asyncio.TimeoutError(), "google": OSError("fixture transport failed"),
    })
    value = run(value_account)
    assert len(calls) == 3
    assert value["saved_sources"][0]["url"] == registered
    assert {row["url"] for row in value["saved_sources"]} == {registered, profile_site}
    assert value["search_results"] == []
    assert value_account == before
    assert "verified" not in value and "account_type" not in value
    assert value.get("search_unavailable") is True
    assert_diagnostics(value)


def test_unregistered_profile_link_is_retained_but_does_not_block_public_discovery(monkeypatch):
    profile_site = "https://unregistered.example.org/"
    value_account = account(metadata={"last_public_profile": {"links": [{"url": profile_site}]}})
    calls = responses(monkeypatch, bing=[result(description="Official Twitter @doro_chiba")])
    value = run(value_account)
    assert len(calls) == 1
    assert profile_site in {row["url"] for row in value["saved_sources"]}
    assert [row["url"] for row in value["search_results"]] == [PUBLIC_SOURCE]
    assert not next(row for row in value["saved_sources"] if row["url"] == profile_site)["trusted"]
    assert_diagnostics(value)


@pytest.mark.parametrize("change", [
    {"platform": "facebook"}, {"platform": "Twitter"}, {"platform": None},
    {"platform": []}, {"platform": {}},
    {"handle": "bad/handle"}, {"handle": "a" * 16}, {"handle": "has space"}, {"handle": "search"},
    {"handle": "a" * 31},
    {"url": "https://x.com/other"}, {"url": "https://www.instagram.com/doro_chiba"},
    {"url": "https://x.com/doro_chiba/status/1"}, {"url": "http://x.com/doro_chiba"},
    {"url": "https://x.com.evil.example.org/doro_chiba"}, {"url": "https://user:password@x.com/doro_chiba"},
    {"url": "https://x.com/doro_chiba?ref=" + "a" * 2100},
    {"metadata": []}, {"metadata": "invalid"}, {"metadata": 3},
    {"name": 3}, {"name": []}, {"name": {}}, {"name": True},
    {"name": "Doro\x00Chiba"}, {"name": "名" * 301},
])
def test_invalid_or_mismatched_target_fails_before_any_search(monkeypatch, change):
    calls = responses(monkeypatch)
    with pytest.raises(ValueError):
        run(account(**change))
    assert calls == []


# Exact public search rows from the first real review. The source is owned QA,
# not a runtime profile, and this literal replay is portable and has no file I/O.
# QA: real-review-20261011T005015Z-1d5450f8/public-review-job.json
# Source SHA-256: 0eef3e44488bb7143d9bd56fbb99b83e448e39d194e77ca4f0cc1f4c6da82913
# JSON paths: result.batches[0].trace[2,3,4].result.search_results
REAL_FIRST_ROUND_IRRELEVANT_SEARCHES = [({'platform': 'twitter', 'handle': 'uc_zengakui', 'name': '全国大学生協連学生委員会', 'url': 'https://x.com/uc_zengakui'},
  [{'url': 'https://www.merriam-webster.com/dictionary/vindicate',
    'title': 'VINDICATE Definition & Meaning - Merriam-Webster',
    'description': 'Vindicate, which has been used in English since at least the mid-16th century, comes from a '
                   'form of the Latin verb vindicare, meaning “to set free, avenge, or lay claim to.”'},
   {'url': 'https://dictionary.cambridge.org/us/dictionary/english/vindicate',
    'title': 'VINDICATE | definition in the Cambridge English Dictionary',
    'description': 'VINDICATE meaning: 1. to prove that what someone said or did was right or true, after other '
                   'people thought it was…. Learn more.'},
   {'url': 'https://englishwordchamps.com/vindicate-meaning-and-examples/',
    'title': 'Vindicate – Meaning and Examples: A Complete Guide to Using and ...',
    'description': 'Using vindicate correctly can be a real boost to your vocabulary toolkit. It’s a word that '
                   'reflects truth, support, justice, and proof—powerful themes in both writing and everyday '
                   'conversations.'},
   {'url': 'https://dictionary.cambridge.org/dictionary/english/vindicate?q=vindicated',
    'title': 'VINDICATE | English meaning - Cambridge Dictionary',
    'description': 'VINDICATE definition: 1. to prove that what someone said or did was right or true, after '
                   'other people thought it was…. Learn more.'},
   {'url': 'https://www.definitions.net/definition/vindicate',
    'title': 'What does vindicate mean? - Definitions.net',
    'description': 'To vindicate means to clear someone of blame, suspicion, or accusations, providing '
                   'justification or proof for their actions or behavior. It can also refer to the act of '
                   'defending or maintaining a right or cause against criticism or affirming its credibility or '
                   'validity.'},
   {'url': 'https://en.thefreedictionary.com/vindicate',
    'title': 'Vindicate - definition of vindicate by The Free Dictionary',
    'description': 'Define vindicate. vindicate synonyms, vindicate pronunciation, vindicate translation, English '
                   'dictionary definition of vindicate. tr.v. vin·di·cat·ed , vin·di·cat·ing , vin·di·cates 1. To '
                   'clear of accusation, blame, suspicion, or doubt with supporting arguments or proof: "Our '
                   'society...'},
   {'url': 'https://www.merriam-webster.com/dictionary/vindication',
    'title': 'VINDICATION Definition & Meaning - Merriam-Webster',
    'description': 'The meaning of VINDICATION is an act of vindicating : the state of being vindicated; '
                   'specifically : justification against denial or censure : defense.'},
   {'url': 'https://www.dictionary.com/browse/%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20vindicate',
    'title': 'VINDICATE Definition & Meaning | Dictionary.com',
    'description': 'Vindicate means to justify, prove, or reinforce an idea — or to absolve from guilt. If your '
                   "family thinks you hogged the last piece of pie on Thanksgiving, you'll be vindicated when "
                   'your younger brother fesses up.'},
   {'url': 'https://www.dictionary.net/dictionary/vindicate',
    'title': 'vindicate Definition & Meaning - Dictionary.net',
    'description': 'To vindicate someone means to free them from blame or suspicion, often by proving they are '
                   'right or justified. Yes, vindicate can be used in legal contexts to refer to the act of '
                   'defending rights or proving innocence. Indeed, vindicate can also imply personal affirmation '
                   "or proving one's self-worth."},
   {'url': 'https://www.collinsdictionary.com/us/dictionary/english/vindicate',
    'title': 'VINDICATE definition in American English | Collins English Dictionary',
    'description': 'If a person or their decisions, actions, or ideas are vindicated, they are proved to be '
                   'correct, after people have said that they were wrong. The director said he had been '
                   "vindicated by the experts' report. He called the success a vindication of his party's "
                   'free-market economic policy. Collins COBUILD Advanced Learner’s Dictionary.'}]),
 ({'platform': 'twitter', 'handle': 'doro_chiba', 'name': 'doro-chiba', 'url': 'https://x.com/doro_chiba'},
  [{'url': 'https://en.wikipedia.org/wiki/Doro_Pesch',
    'title': 'Doro Pesch - Wikipedia',
    'description': 'Dorothee Pesch (born 3 June 1964), known professionally as Doro Pesch or simply Doro, is a '
                   "German heavy metal singer and the former frontwoman of heavy metal band Warlock. Doro's "
                   'contributions to music and culture made her a global figure in metal culture for over four '
                   'decades.'},
   {'url': 'https://www.doro.com/en-us/shop/mobile-devices/',
    'title': '- Doro',
    'description': 'We make simple and smart products for our seniors! by. Menu. Close. Products. Why Doro. –who '
                   'wants to be a junior? –User portraits. –Meet EVA. Support. –Smartphone school. –Common '
                   'charger directive. Search. What are you searching for? Corporate information. About Doro. '
                   'Investors. Partner area. Social media. About the website. Change country.'},
   {'url': 'https://www.doro.com/',
    'title': 'Doro',
    'description': 'Discover our smartphones, phones and services adapted for the elderly, as easy as they are '
                   'safe to use.'},
   {'url': 'https://www.dororg.com/doro-marketplace',
    'title': 'DORO MARKETPLACE — DORO Restaurant Group',
    'description': 'Inspired by the cafes and bakeries of Europe, we seek to bring the best of DORO to our '
                   'Marketplace to provide you with the nourishment you need to flourish, whether with us or at '
                   'home. Visit Marketplace’s website and full view menu here.'},
   {'url': 'https://www.doromusic.de/updates.html',
    'title': 'DORO - The Official Website',
    'description': "14/11/2026 21:00 St. Julian's, Malta Malta Metal Weekend 2026 Buy Ticket Now"},
   {'url': 'https://www.doromkpl.com/',
    'title': 'doromarketplace',
    'description': 'Based in West Hartford, Connecticut, DORO Restaurant Group operates multiple restaurants '
                   'including Treva, Àvert, Artisanal Burger Company, Zohara, DORO Marketplace, Casadoro, and No '
                   'Mames.'},
   {'url': 'https://doromusic.com/',
    'title': 'DORO - The Official Website',
    'description': 'In 1986, DORO became the first woman to perform at the legendary Monsters of Rock festivals. '
                   'After Warlock disbanded, DORO successfully continued her career as a solo artist and built an '
                   'impressive international fan base.'},
   {'url': 'https://www.dororg.com/',
    'title': 'DORO Restaurant Group',
    'description': 'Based in West Hartford, Connecticut, the DORO Restaurant Group owns and operates multiple '
                   'restaurants including Treva, Àvert, ABC Artisanal Burger Company and Zohara.'},
   {'url': 'https://www.youtube.com/watch?v=A857YBw8u30',
    'title': 'DORO - Children Of The Dawn (OFFICIAL MUSIC VIDEO)',
    'description': "The official video for Doro's brand new single 'Children Of The Dawn', off the album "
                   'Conqueress: Forever Strong and Proud, out now.'},
   {'url': 'https://en.wikipedia.org/wiki/Doro_%28company%29',
    'title': 'Doro (company) - Wikipedia',
    'description': 'Doro AB, known as Doro, is a Swedish consumer electronics and assistive technology company '
                   'focused on the elderly and improving the lives of seniors.'}]),
 ({'platform': 'twitter',
   'handle': 'ku_zenshotai',
   'name': '京都大学全学処分対策委員会（全処対）',
   'url': 'https://x.com/ku_zenshotai'},
  [{'url': 'https://www.unitconverters.net/length/m-to-cm.htm',
    'title': 'Convert m to cm',
    'description': 'Instant free online tool for meter to centimeter conversion or vice versa. The meter [m] to '
                   'centimeter [cm] conversion table and conversion steps are also listed.'},
   {'url': 'https://www.unitconverters.net/length/cm-to-m.htm',
    'title': 'Convert cm to m',
    'description': 'Instant free online tool for centimeter to meter conversion or vice versa. The centimeter '
                   '[cm] to meter [m] conversion table and conversion steps are also listed. Also, explore tools '
                   'to convert centimeter or meter to other length units or learn more about length conversions.'},
   {'url': 'https://www.symbolab.com/calculator/construction/meters_centimeters_calculator',
    'title': 'Meters and Centimeters Calculator - Symbolab',
    'description': 'Easily convert between meters and centimeters with our user-friendly calculator. Perfect for '
                   'quick calculations, homework, and measurement conversions.'},
   {'url': 'https://www.digicalc.net/length-converter',
    'title': 'Length Converter - mm, cm, m, km, Inches, Feet | DigiCalc',
    'description': 'The converter supports all combinations across metric and imperial units, including '
                   'millimeters, centimeters, meters, kilometers, inches, feet, yards, and miles.'},
   {'url': 'https://www.wolframalpha.com/input/?i=10+meter+-+5+centimeter',
    'title': '10 meter - 5 centimeter - Wolfram|Alpha',
    'description': "Compute answers using Wolfram's breakthrough technology & knowledgebase, relied on by "
                   'millions of students & professionals. For math, science, nutrition, history, geography, '
                   'engineering, mathematics, linguistics, sports, finance, music…'},
   {'url': 'https://www.metric-conversions.org/length/meters-to-centimeters.htm',
    'title': 'Meter to Cm conversion: meters to centimeters calculator',
    'description': 'To convert meters to centimeters, you can use the formula: centimeters = meters x 100. For '
                   "example, let's say we have a length of 2 meters that we want to convert to centimeters. By "
                   'using the formula, we can calculate that 2 meters is equal to 200 centimeters.'},
   {'url': 'https://www.math-only-math.com/units-of-length-conversion-charts.html',
    'title': 'Units of Length Conversion Charts',
    'description': 'Units of length conversion charts are discussed here in metric units of length and customary '
                   'units of length: In math when we use length, we know that the standard unit of length is '
                   '‘Metre’ which is written in short as ‘m’. A metre length is divided into 100 equal parts.'},
   {'url': 'https://www.omnicalculator.com/conversion/centimeter',
    'title': 'Centimeter Calculator',
    'description': 'The handy centimeter calculator helps you determine length units in different systems such as '
                   'the metric and the imperial one.'},
   {'url': 'https://www.inchcalculator.com/convert/meter-to-centimeter/',
    'title': 'Meters to Centimeters Converter (m to cm) - Inch Calculator',
    'description': 'Convert meters to centimeters (m to cm) with the length conversion calculator, and learn the '
                   'meter to centimeter formula.'},
   {'url': 'https://num8ers.com/metric-length-conversion/',
    'title': 'Metric Length Conversion Calculator 2026 | Try Instantly – NUM8ERS',
    'description': 'Free metric length converter with all SI prefixes. Convert between meters, kilometers, '
                   'centimeters, millimeters, micrometers, nanometers, and more. Complete metric system guide.'}])]


@pytest.mark.parametrize("target,items", REAL_FIRST_ROUND_IRRELEVANT_SEARCHES,
                         ids=["uc_zengakui-vindicate-dictionaries", "doro_chiba-singer-and-products", "ku_zenshotai-unit-converters"])
@pytest.mark.parametrize("fallback_has_relevant", [False, True], ids=["honest-empty", "relevant-fallback"])
def test_exact_first_real_round_irrelevant_results_are_rejected_and_do_not_block_fallback(monkeypatch, target, items, fallback_has_relevant):
    relevant = result(description=f"Official Twitter account @{target['handle']}")
    calls = responses(monkeypatch, bing=items, duck=[relevant] if fallback_has_relevant else [])
    value = run(account(**target))
    assert [provider(url) for url, _ in calls] == (["bing", "ddg"] if fallback_has_relevant else ["bing", "ddg", "google"])
    first = value["diagnostics"][0]
    assert (first["raw_count"], first["relevant_count"], first["rejected_count"]) == (10, 0, 10)
    assert all(set(row) == {"reason"} for row in first["rejected"])
    assert first["fallback_reason"]
    assert [row["url"] for row in value["search_results"]] == ([PUBLIC_SOURCE] if fallback_has_relevant else [])
    assert_diagnostics(value)


@pytest.mark.parametrize("value", [[], "twitter:doro_chiba", 3, None])
def test_non_dictionary_input_is_rejected_before_any_search(monkeypatch, value):
    calls = responses(monkeypatch)
    with pytest.raises(ValueError):
        asyncio.run(search.search_account_public(value))
    assert calls == []
