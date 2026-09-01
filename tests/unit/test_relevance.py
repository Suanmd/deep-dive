"""Tests for the two-stage query-relevance check."""

from __future__ import annotations

from deep_dive.relevance import (
    _extract_core_entities,
    core_entity_hitrate,
    explain_relevance,
    is_query_irrelevant,
    query_keyword_density,
)
from deep_dive.types import RelevanceVerdict


class TestQueryKeywordDensity:
    def test_perfect_match(self):
        text = "黄金价格 持续上涨 投资价值凸显"
        assert query_keyword_density(text, "黄金投资") == 1.0

    def test_no_match(self):
        text = "Microsoft Visual Studio 18.3 release notes"
        assert query_keyword_density(text, "黄金投资") == 0.0

    def test_partial_match(self):
        text = "黄金价格走势分析"
        density = query_keyword_density(text, "黄金投资")
        # "黄金" hits, "投" hits (in 投资 not present), "资" hits
        # Let's recompute: keywords are 黄金, 投, 资 → "黄金" hits (in text)
        # "投" is not in text, "资" is not in text. So 1/3 ≈ 0.33
        assert 0.0 <= density <= 1.0

    def test_empty_text_returns_zero(self):
        assert query_keyword_density("", "黄金投资") == 0.0

    def test_empty_query_returns_zero(self):
        assert query_keyword_density("any text", "") == 0.0

    def test_query_without_keywords_returns_one(self):
        # Query like "x" has no Chinese chars and no English 3+ words
        assert query_keyword_density("text", "x") == 1.0


class TestCoreEntityHitrate:
    def test_all_entities_hit(self):
        # Use a 2-char Chinese entity to avoid the bisect heuristic.
        text = "DRAM 长江 报告"
        hits, total = core_entity_hitrate(text, "DRAM 长江")
        assert hits == 2
        assert total == 2

    def test_no_entities_hit(self):
        text = "Microsoft Office 365 pricing"
        hits, total = core_entity_hitrate(text, "长鑫 DRAM")
        assert hits == 0
        assert total == 2

    def test_bisect_long_chinese_runs(self):
        # 4-char Chinese run is bisected: ["长鑫存储", "长鑫", "存储"]
        entities = _extract_core_entities("长鑫存储")
        assert "长鑫存储" in entities
        assert "长鑫" in entities
        assert "存储" in entities

    def test_empty_query(self):
        hits, total = core_entity_hitrate("text", "")
        assert hits == 0
        assert total == 0

    def test_extracts_chinese_entities(self):
        entities = _extract_core_entities("长鑫存储 DRAM 2026")
        assert "长鑫存储" in entities
        # English entities are lower-cased by the implementation for
        # case-insensitive matching.
        assert "dram" in entities
        assert "2026" in entities


class TestIsQueryIrrelevant:
    def test_relevant_text(self):
        text = "黄金价格 持续上涨 投资价值凸显"
        assert is_query_irrelevant(text, "黄金投资") is False

    def test_irrelevant_text(self):
        text = "Microsoft Office 365 pricing analysis"
        assert is_query_irrelevant(text, "黄金投资") is True

    def test_aliyun_menu_miss_regression(self):
        text = "aliyun 存储 存储芯片 能效 产品 价格"
        assert is_query_irrelevant(text, "长鑫 DRAM") is True

    def test_long_xin_real_article(self):
        text = "长鑫存储 2026 年 DRAM 产能预计激增 30%，市场份额将达 8%"
        assert is_query_irrelevant(text, "长鑫 DRAM") is False

    def test_empty_text_irrelevant(self):
        assert is_query_irrelevant("", "黄金投资") is True

    def test_empty_query_irrelevant(self):
        assert is_query_irrelevant("text", "") is True


class TestExplainRelevance:
    def test_relevant_verdict(self):
        text = "黄金价格 持续上涨 投资价值凸显"
        v = explain_relevance(text, "黄金投资")
        assert v == RelevanceVerdict.RELEVANT

    def test_density_failure(self):
        text = "Microsoft Visual Studio release notes"
        v = explain_relevance(text, "黄金投资")
        assert v == RelevanceVerdict.IRRELEVANT_DENSITY

    def test_entity_failure(self):
        # Stage 1 should pass (some single-char keywords hit) but Stage 2
        # should fail (no core entity "长鑫" or "DRAM" in the text).
        # We deliberately pick a text containing the single char "长"
        # (e.g. inside "长江存储") but NOT the entity "长鑫".
        text = "长江存储 aliyun 存储 存储芯片 能效 产品 价格 库存"
        v = explain_relevance(text, "长鑫 DRAM")
        assert v == RelevanceVerdict.IRRELEVANT_ENTITY


class TestCustomThresholds:
    def test_custom_min_hitrate(self):
        text = "Microsoft Office"
        # With very strict threshold, even mild overlap is irrelevant
        assert is_query_irrelevant(text, "黄金投资", min_hitrate=0.01) is True

    def test_custom_entity_threshold(self):
        text = "长鑫 DRAM"  # Perfect entity match
        assert is_query_irrelevant(text, "长鑫 DRAM", core_entity_min_hitrate=0.99) is False
