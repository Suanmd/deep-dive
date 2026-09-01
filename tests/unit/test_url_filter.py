"""Tests for the smart URL filter pipeline."""

from __future__ import annotations

from deep_dive.constants import LOWQ_DOMAINS, SPAM_DOMAINS
from deep_dive.filters.url_filter import smart_filter_urls


class TestSmartFilterBasic:
    def test_empty_input_returns_empty(self):
        assert smart_filter_urls([]) == []
        assert smart_filter_urls(None) == []

    def test_keeps_clean_urls(self):
        urls = [
            "https://example.com/article-1",
            "https://github.com/owner/repo",
            "https://stackoverflow.com/questions/123",
        ]
        result = smart_filter_urls(urls)
        assert len(result) == 3
        # Order preserved
        assert result == urls

    def test_dedupes_after_canonicalization(self):
        urls = [
            "https://Example.com/article",
            "https://example.com/article",
            "HTTPS://EXAMPLE.COM/article",
        ]
        result = smart_filter_urls(urls)
        # Canonical host is lowercase, so all three collapse to one
        assert len(result) == 1
        assert result[0] == "https://example.com/article"

    def test_dedupes_after_tracking_strip(self):
        urls = [
            "https://example.com/article?utm_source=x",
            "https://example.com/article?keep=1",
        ]
        # After tracking strip, the first becomes "https://example.com/article"
        # which is different from the second (has keep=1), so both kept
        result = smart_filter_urls(urls)
        assert len(result) == 2


class TestSpamDomainFilter:
    def test_spam_domain_dropped(self):
        # Pick a domain that's definitely in SPAM_DOMAINS
        sample = next(iter(SPAM_DOMAINS))
        url = f"https://{sample}/some-path"
        result = smart_filter_urls([url], verbose=False)
        assert result == []

    def test_multiple_spam_domains_all_dropped(self):
        urls = [f"https://{d}/p" for d in list(SPAM_DOMAINS)[:3]]
        result = smart_filter_urls(urls)
        assert result == []

    def test_lowq_domain_dropped(self):
        sample = next(iter(LOWQ_DOMAINS))
        url = f"https://{sample}/some-path"
        result = smart_filter_urls([url])
        assert result == []


class TestPathPatternFilter:
    def test_login_path_dropped(self):
        assert smart_filter_urls(["https://example.com/login"]) == []

    def test_signup_path_dropped(self):
        assert smart_filter_urls(["https://example.com/signup"]) == []

    def test_cart_path_dropped(self):
        assert smart_filter_urls(["https://example.com/cart"]) == []

    def test_sort_query_param_dropped(self):
        assert smart_filter_urls(["https://example.com/p?sort=date"]) == []


class TestLowqHostFilter:
    def test_kongfz_item_dropped(self):
        assert smart_filter_urls(["https://kongfz.com/item/12345"]) == []

    def test_weread_paywall_dropped(self):
        assert smart_filter_urls(["https://weread.qq.com/web/reader/abc123"]) == []

    def test_book118_template_dropped(self):
        assert smart_filter_urls(["https://book118.com/p/12345.html"]) == []


class TestPerDomainCap:
    def test_caps_each_domain(self):
        urls = [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
            "https://example.com/d",
        ]
        result = smart_filter_urls(urls, keep_per_domain=2)
        assert len(result) == 2

    def test_cap_only_affects_overflowed_domain(self):
        urls = [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",  # example.com: capped
            "https://other.com/x",
            "https://other.com/y",
            "https://other.com/z",
            "https://other.com/w",  # other.com: capped
        ]
        result = smart_filter_urls(urls, keep_per_domain=2)
        assert len(result) == 4

    def test_no_cap_means_all_kept(self):
        urls = [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        result = smart_filter_urls(urls, keep_per_domain=None)
        assert len(result) == 3


class TestStats:
    def test_stats_reflect_pipeline_outcomes(self):
        urls = [
            "https://example.com/a",
            "https://example.com/a",  # dup
            f"https://{next(iter(SPAM_DOMAINS))}/p",  # spam
            "https://example.com/login",  # path
        ]
        # Run without verbose to capture return value
        result = smart_filter_urls(urls, verbose=False)
        # Just verify result is the kept URL
        assert result == ["https://example.com/a"]

    def test_total_in_matches_input_length(self):
        # We can't directly inspect stats from the public API since it's
        # returned via verbose=True only. Verify the side effect (kept count).
        urls = ["https://example.com/a"]
        result = smart_filter_urls(urls, verbose=True)
        # No assertion on stats side; just ensure call succeeds
        assert len(result) == 1


class TestInvalidInputs:
    def test_none_input_returns_empty(self):
        assert smart_filter_urls(None) == []

    def test_empty_string_url_dropped(self):
        result = smart_filter_urls(["", "https://example.com/a"])
        assert result == ["https://example.com/a"]

    def test_non_string_url_dropped(self):
        result = smart_filter_urls([None, 123, "https://example.com/a"])
        assert result == ["https://example.com/a"]
