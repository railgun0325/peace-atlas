from app.clean import clean_article_text
from app.dedupe import content_fingerprint, normalize_url


def test_normalize_url_removes_tracking_params():
    url = "https://Example.com/news/123/?utm_source=x&b=2&a=1&fbclid=abc"
    assert normalize_url(url) == "https://example.com/news/123?a=1&b=2"


def test_content_fingerprint_stable_after_html_cleaning():
    clean_a = clean_article_text("标题", "<article><p>第一段</p><script>x()</script></article>")
    clean_b = clean_article_text("标题", "第一段")
    assert content_fingerprint("标题", clean_a) == content_fingerprint("标题", clean_b)
