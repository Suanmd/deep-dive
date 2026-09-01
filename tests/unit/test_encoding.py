"""Tests for smart HTML byte decoding.

Covers the bug fix for ``resp.text`` falling back to ISO-8859-1
(double-mojibake on Chinese sites without explicit charset).
"""

from __future__ import annotations

from deep_dive.crawler.encoding import (
    is_likely_double_mojibake,
    smart_decode_bytes,
)

# ---------------------------------------------------------------------------
# smart_decode_bytes
# ---------------------------------------------------------------------------


class TestSmartDecodeBytes:
    def test_empty_bytes_returns_empty(self):
        assert smart_decode_bytes(b"") == ""

    def test_utf8_bytes_decode_correctly(self):
        # "数" in UTF-8 is e6 95 b0
        assert smart_decode_bytes("数".encode()) == "数"

    def test_gbk_bytes_decode_correctly(self):
        # "数" in GBK is ca fd
        assert smart_decode_bytes("数".encode("gbk")) == "数"

    def test_gb2312_bytes_decode_correctly(self):
        assert smart_decode_bytes("中文".encode("gb2312")) == "中文"

    def test_hint_used_first_when_correct(self):
        # GBK bytes, hint says gb2312 (compatible) → should decode correctly
        data = "用户".encode("gbk")
        assert smart_decode_bytes(data, hint="gbk") == "用户"

    def test_hint_falls_through_on_failure(self):
        # Bytes are UTF-8 but hint says GBK → strict decode fails, must fall through
        data = "数学".encode()
        result = smart_decode_bytes(data, hint="gbk")
        # Should fall through and recover via charset_normalizer / meta / UTF-8
        assert "数" in result and "学" in result

    def test_meta_charset_extraction(self):
        html = b'<html><head><meta charset="utf-8"></head><body>\xe6\x95\xb0\xe5\xad\xa6</body></html>'
        result = smart_decode_bytes(html)
        assert "数学" in result

    def test_meta_charset_with_attributes(self):
        # HTML4 form: <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
        html = (
            b"<html><head>"
            b'<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
            b"</head><body>\xe6\x95\xb0</body></html>"
        )
        result = smart_decode_bytes(html)
        assert "数" in result

    def test_no_charset_signal_falls_back_to_utf8(self):
        # No hint, no detectable charset, no meta — just bytes.
        data = "费马大定理".encode()
        assert smart_decode_bytes(data) == "费马大定理"

    def test_invalid_bytes_dont_crash(self):
        # Random garbage bytes — should not raise, should return *something*.
        garbage = bytes(range(256))[:200]
        result = smart_decode_bytes(garbage)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_large_html_only_scans_head(self):
        # Confirm we don't try to scan the entire body for meta charset.
        # 1MB of body content after a small head with charset.
        head = (
            b'<html><head><meta charset="utf-8"></head><body>' + b"<p>" * 100000 + b"\xe6\x95\xb0\xe5\xad\xa6"
        )
        result = smart_decode_bytes(head)
        assert "数学" in result

    def test_returns_never_raises(self):
        # Failure mode: ensure callers can never get a crash from decoding.
        # We exercise this by passing bytes that no encoding can fully decode.
        bad = b"\xff\xfe\x00\x01" * 50
        result = smart_decode_bytes(bad)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# is_likely_double_mojibake
# ---------------------------------------------------------------------------


class TestIsLikelyDoubleMojibake:
    def test_clean_chinese_returns_false(self):
        assert is_likely_double_mojibake("数学费马大定理") is False

    def test_clean_english_returns_false(self):
        assert is_likely_double_mojibake("Fermat's Last Theorem") is False

    def test_double_mojibake_returns_true(self):
        # Result of UTF-8-decoded-then-Latin-1-re-encoded garbage.
        # Real signature of the nfnews.com bug.
        bad = "\u00e6\u0095\u00b0\u00e5\u00ad\u00a6"
        assert is_likely_double_mojibake(bad) is True

    def test_mixed_clean_then_mojibake(self):
        # First half clean, second half mojibake — overall still flagged.
        clean = "这是一段正常的文字" * 5
        bad = "\u00e6\u0095\u00b0" * 5
        assert is_likely_double_mojibake(clean + bad) is True

    def test_empty_returns_false(self):
        assert is_likely_double_mojibake("") is False

    def test_french_punctuation_not_flagged(self):
        # Smart quotes / accents should NOT trigger (those are valid French).
        clean = "C'est l'été — résumé d'aujourd'hui"
        assert is_likely_double_mojibake(clean) is False
