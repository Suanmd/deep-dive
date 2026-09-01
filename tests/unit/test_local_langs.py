"""Tests for local-language detection."""

from __future__ import annotations

from deep_dive.local_langs import (
    DEFAULT_RULES,
    detect_local_langs,
    local_lang_for,
)


class TestDetectLocalLangs:
    def test_empty_returns_empty(self):
        assert detect_local_langs("") == []

    def test_japan_detected(self):
        result = detect_local_langs("日本 东京 旅行")
        codes = [ll.code for ll in result]
        assert "ja" in codes

    def test_south_america_spanish(self):
        result = detect_local_langs("南美历史")
        codes = [ll.code for ll in result]
        assert "es" in codes

    def test_france_french(self):
        result = detect_local_langs("巴黎 法国大革命")
        codes = [ll.code for ll in result]
        assert "fr" in codes

    def test_germany_german(self):
        result = detect_local_langs("germany berlin")
        codes = [ll.code for ll in result]
        assert "de" in codes

    def test_india_hindi(self):
        result = detect_local_langs("India Mumbai tech")
        codes = [ll.code for ll in result]
        assert "hi" in codes

    def test_no_match_returns_empty(self):
        assert detect_local_langs("Python asyncio tutorial") == []

    def test_dedupes_by_language_code(self):
        # "巴西" and "brazil" both match different rules (es + pt)
        # but we want at most one per code
        result = detect_local_langs("巴西 brazil south america")
        codes = [ll.code for ll in result]
        # es appears at most once
        assert codes.count("es") <= 1
        assert codes.count("pt") <= 1

    def test_case_insensitive(self):
        result_lower = detect_local_langs("japan tokyo")
        result_upper = detect_local_langs("JAPAN TOKYO")
        assert [ll.code for ll in result_lower] == [ll.code for ll in result_upper]


class TestLocalLangFor:
    def test_known_code(self):
        ll = local_lang_for("ja")
        assert ll is not None
        assert ll.code == "ja"
        assert ll.name == "日文"

    def test_unknown_code(self):
        assert local_lang_for("xx") is None


class TestDefaultRules:
    def test_at_least_ten_rules(self):
        assert len(DEFAULT_RULES) >= 10
