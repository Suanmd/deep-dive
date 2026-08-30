"""Tests for the domain blacklist helpers."""

from __future__ import annotations

from deep_dive.crawler.blacklist import (
    is_baidu_domain,
    is_black_domain,
    is_lowq_domain,
    is_spam_domain,
)


class TestSpamDomain:
    def test_doc88_is_spam(self):
        assert is_spam_domain("https://doc88.com/p/12345") is True

    def test_amazon_is_spam(self):
        assert is_spam_domain("https://amazon.com/some-product") is True

    def test_github_is_not_spam(self):
        assert is_spam_domain("https://github.com/owner/repo") is False

    def test_taobao_is_spam(self):
        assert is_spam_domain("https://taobao.com/item/123") is True

    def test_subdomain_matches(self):
        # Substring match — www.taobao.com still matches "taobao.com"
        assert is_spam_domain("https://www.taobao.com/item/123") is True

    def test_invalid_url_returns_false(self):
        assert is_spam_domain("not a url") is False


class TestBlackDomain:
    def test_goodreads_is_black(self):
        assert is_black_domain("https://goodreads.com/book/123") is True

    def test_weread_is_black(self):
        assert is_black_domain("https://weread.qq.com/web/reader/abc") is True

    def test_normal_site_not_black(self):
        assert is_black_domain("https://example.com/article") is False


class TestLowqDomain:
    def test_k73_is_lowq(self):
        assert is_lowq_domain("https://k73.com/game/123") is True

    def test_book118_is_lowq(self):
        assert is_lowq_domain("https://book118.com/p/12345.html") is True

    def test_github_is_not_lowq(self):
        assert is_lowq_domain("https://github.com/owner/repo") is False


class TestBaiduDomain:
    def test_baike_baidu(self):
        assert is_baidu_domain("https://baike.baidu.com/item/123") is True

    def test_zhidao_baidu(self):
        assert is_baidu_domain("https://zhidao.baidu.com/question/123") is True

    def test_baijiahao(self):
        assert is_baidu_domain("https://baijiahao.baidu.com/s?id=123") is True

    def test_baidu_search(self):
        assert is_baidu_domain("https://www.baidu.com/s?wd=test") is True

    def test_github_is_not_baidu(self):
        assert is_baidu_domain("https://github.com/owner/repo") is False
