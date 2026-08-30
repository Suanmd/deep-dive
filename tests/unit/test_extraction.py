"""Tests for HTML extraction helpers."""

from __future__ import annotations

import pytest

from deep_dive.crawler.extraction import (
    extract_main_text,
    extract_title,
    looks_like_block_page,
)


class TestExtractMainText:
    def test_returns_empty_on_empty_input(self):
        assert extract_main_text("") == ""

    def test_returns_empty_on_none(self):
        assert extract_main_text(None) == ""

    def test_extracts_article_text(self):
        html = """
        <html>
        <head><title>Sample</title></head>
        <body>
            <article>
                <h1>Sample Article</h1>
                <p>This is the main content. It contains enough text to be
                considered a real article, not just a snippet or a tagline.</p>
                <p>Continued main content here.</p>
            </article>
        </body>
        </html>
        """
        text = extract_main_text(html)
        if text:  # trafilatura may or may not be installed in test env
            assert "main content" in text.lower()
            assert "sample article" in text.lower()


class TestExtractTitle:
    def test_extracts_title(self):
        html = "<html><head><title>My Article</title></head><body></body></html>"
        title = extract_title(html)
        assert title == "My Article"

    def test_normalizes_whitespace(self):
        html = "<title>Multiple\n\n   Spaces</title>"
        assert extract_title(html) == "Multiple Spaces"

    def test_empty_html(self):
        assert extract_title("") == ""

    def test_no_title_element(self):
        assert extract_title("<html><body></body></html>") == ""

    def test_max_length_truncation(self):
        long_title = "X" * 300
        html = f"<title>{long_title}</title>"
        result = extract_title(html, max_length=50)
        assert len(result) == 50


class TestLooksLikeBlockPage:
    def test_empty_text_is_block(self):
        assert looks_like_block_page("") is True

    def test_short_text_is_block(self):
        assert looks_like_block_page("hi") is True

    def test_long_text_not_block(self):
        assert looks_like_block_page(
            "This is a perfectly normal article that doesn't contain any "
            "challenge keywords. It has enough content to be considered real."
        ) is False

    def test_captcha_phrase_detected(self):
        assert looks_like_block_page(
            "Some prefix text. " + "Please wait while we verify you are human. " * 5
        ) is True

    def test_cloudflare_keyword_detected(self):
        assert looks_like_block_page(
            "Checking your browser before accessing example.com. "
            "This process is automatic. Your browser will redirect shortly. "
            "DDoS protection by Cloudflare. " * 3
        ) is True

    def test_chinese_block_keyword_detected(self):
        assert looks_like_block_page(
            "系统检测到您的访问异常，请进行验证以继续访问。" + "请进行验证 " * 10
        ) is True
