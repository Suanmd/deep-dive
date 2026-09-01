"""Tests for the stage-3 topic-drift guard.

Covers ``_extract_primary_entity``, ``_primary_in_lead``, and the
``IRRELEVANT_LEAD`` verdict in :func:`explain_relevance` /
:func:`is_query_irrelevant`.
"""

from __future__ import annotations

from deep_dive.relevance import (
    _extract_primary_entity,
    _primary_in_lead,
    explain_relevance,
    is_query_irrelevant,
)
from deep_dive.types import RelevanceVerdict

# ---------------------------------------------------------------------------
# _extract_primary_entity
# ---------------------------------------------------------------------------


class TestExtractPrimaryEntity:
    def test_first_long_chinese_entity(self):
        assert _extract_primary_entity("费马大定理 证明 怀尔斯") == "费马大定理"

    def test_skips_short_verbs(self):
        # "证明" is 2 chars (below PRIMARY_MIN_LEN=3) — should be skipped
        # so "费马大定理" becomes primary even when it's not first.
        assert _extract_primary_entity("证明 费马大定理") == "费马大定理"

    def test_no_qualifying_entity_returns_none(self):
        assert _extract_primary_entity("费马") is None  # 2 chars
        assert _extract_primary_entity("证明") is None  # 2 chars, generic

    def test_english_only_query_returns_none(self):
        assert _extract_primary_entity("DRAM market 2026") is None
        assert _extract_primary_entity("Wiles proof modular") is None

    def test_empty_query_returns_none(self):
        assert _extract_primary_entity("") is None

    def test_picks_first_when_multiple_qualifying(self):
        # If query has multiple ≥3-char Chinese entities, the first one wins.
        assert _extract_primary_entity("谷山志村猜想 朗兰兹纲领 怀尔斯") == "谷山志村猜想"

    def test_custom_min_len(self):
        assert _extract_primary_entity("费马 怀尔斯", min_len=2) == "费马"
        assert _extract_primary_entity("费马 怀尔斯", min_len=3) == "怀尔斯"


# ---------------------------------------------------------------------------
# _primary_in_lead
# ---------------------------------------------------------------------------


class TestPrimaryInLead:
    def test_primary_in_short_text(self):
        # Short text, cutoff = max(500, ...) = 500. Primary in text.
        assert _primary_in_lead("介绍费马大定理的内容", "费马大定理") is True

    def test_primary_not_in_short_text(self):
        assert _primary_in_lead("其他无关内容", "费马大定理") is False

    def test_lead_uses_500_char_floor(self):
        # 600-char text: cutoff = max(500, int(600*0.3)) = max(500, 180) = 500.
        # Primary placed at char 600+ (past the 500-char floor) is NOT in lead.
        text = "x" * 600 + "费马大定理"
        assert _primary_in_lead(text, "费马大定理") is False
        # Sanity check: same primary at start IS in lead.
        assert _primary_in_lead("费马大定理" + "x" * 600, "费马大定理") is True

    def test_empty_text_returns_true(self):
        # vacuous pass — no text to be irrelevant to
        assert _primary_in_lead("", "费马大定理") is True

    def test_empty_primary_returns_true(self):
        assert _primary_in_lead("一些文字", "") is True

    def test_bisect_halves_accepted(self):
        # "黄金投资" doesn't appear as full string in text,
        # but half "黄金" does — should pass (matches bisect semantics).
        text = "黄金价格 持续上涨 投资价值凸显" * 1  # ~15 chars
        assert _primary_in_lead(text, "黄金投资") is True

    def test_long_text_does_not_split_bisect(self):
        # 5-char primary "费马大定理": half "费马" is a prefix of full entity.
        # If the full entity isn't in lead, the half-prefix can't be either.
        # Place primary at char 3000+ so it's past lead (3000 > 30% of 8005).
        text = "x" * 3000 + "费马大定理" + "y" * 5000
        assert _primary_in_lead(text, "费马大定理") is False

    def test_custom_lead_frac(self):
        # Tight lead (10%) on a 5000-char text → cutoff = 500.
        text = "x" * 499 + "费马大定理" + "x" * 4500
        assert _primary_in_lead(text, "费马大定理", lead_frac=0.1) is False
        assert _primary_in_lead(text, "费马大定理", lead_frac=0.5) is True


# ---------------------------------------------------------------------------
# is_query_irrelevant (stage 3)
# ---------------------------------------------------------------------------


class TestIsQueryIrrelevantStage3:
    def test_relevant_flint_article(self):
        # Genuine FLT article: "费马大定理" appears in first 100 chars.
        text = (
            "费马大定理（Fermat's Last Theorem）是数论中最著名的未解之谜之一。"
            "本文介绍其历史背景、证明思路与数学意义。" + "更多内容" * 100
        )
        assert is_query_irrelevant(text, "费马大定理 证明 怀尔斯") is False

    def test_langlands_article_filtered_by_stage3(self):
        # Realistic test for the topic-drift bug:
        # Query is "费马大定理 证明 怀尔斯 谷山志村 朗兰兹纲领"
        # Article is ~5300 chars about Geometric Langlands breakthrough;
        # mentions FLT only briefly in section 3 (mid-body, ~char 3500+).
        # Without stage 3, the article passes via entity coverage of
        # "朗兰兹纲领" + "怀尔斯". With stage 3, primary "费马大定理"
        # is not in the first 1590 chars → filtered.
        flt_mention = (
            "在数论中，有一类很经典的问题，就是多项式方程的整数解，"
            "以及更进一步地，素数解的存在性和解的数量。"
            "例如，费马大定理的内容就是多项式方程x^n+y^n=z^n当n大于2时不存在整数解。"
        )
        # Body structure: long intro about Langlands, then FLT mention much later.
        intro = (
            "在几何化朗兰兹猜想提出二十多年后，九位数学家第一次给出了"
            "这一宏大猜想的精确描述。这一最新成果或将成为数学界三十年"
            "努力的巅峰。"
            * 5  # ~150 chars, lead
            + flt_mention  # appears around char 150+
            + "其余内容关于朗兰兹纲领。" * 50  # pad to ~3000+ chars
        )
        # Wait — with this construction flt_mention is at char ~150 (well within
        # lead of 500 chars). Let me restructure to push FLT past the lead.
        intro = "在几何化朗兰兹猜想提出二十多年后，" * 60  # ~1500 chars intro
        mid = "其他朗兰兹历史段落。" * 30  # ~500 chars
        later = flt_mention + "更多朗兰兹讨论。" * 30  # FLT mention near char 2500+
        full_text = intro + mid + later

        assert is_query_irrelevant(full_text, "费马大定理 证明 怀尔斯 谷山志村 朗兰兹纲领") is True

    def test_opt_out_restores_v5_2_0_behaviour(self):
        # Same Langlands-article scenario, but with stage 3 disabled.
        # Should pass (two-stage check is satisfied).
        flt_mention = "费马大定理的内容就是多项式方程..."
        intro = "朗兰兹纲领" * 200  # 2000 chars of Langlands content
        full_text = intro + flt_mention

        assert (
            is_query_irrelevant(
                full_text,
                "费马大定理 证明 怀尔斯 谷山志村 朗兰兹纲领",
                require_primary_in_lead=False,
            )
            is False
        )

    def test_short_query_unchanged(self):
        # 2-char query can't have primary ≥3 chars → stage 3 vacuous pass.
        assert is_query_irrelevant("费马大定理的证明", "费马") is False

    def test_english_query_unchanged(self):
        # English-only query → no primary → vacuous pass.
        text = "DRAM market analysis and pricing trends for 2026"
        assert is_query_irrelevant(text, "DRAM market") is False


# ---------------------------------------------------------------------------
# explain_relevance (IRRELEVANT_LEAD verdict)
# ---------------------------------------------------------------------------


class TestExplainRelevanceStage3:
    def test_irrelevant_lead_verdict(self):
        intro = "朗兰兹纲领的内容与几何化猜想" * 200
        flt_mention = "例如，费马大定理"
        full_text = intro + flt_mention + "继续讨论" * 30

        v = explain_relevance(full_text, "费马大定理 朗兰兹纲领")
        assert v == RelevanceVerdict.IRRELEVANT_LEAD

    def test_relevant_when_primary_in_lead(self):
        text = "费马大定理的内容与证明。" * 10
        v = explain_relevance(text, "费马大定理 朗兰兹纲领")
        assert v == RelevanceVerdict.RELEVANT

    def test_irrelevant_density_still_first(self):
        # Stage 1 should still fire first even when stage 3 would also fire.
        # (Density failure takes precedence; we don't even reach stage 3.)
        text = "Microsoft Visual Studio release notes" * 5
        v = explain_relevance(text, "费马大定理")
        assert v == RelevanceVerdict.IRRELEVANT_DENSITY

    def test_irrelevant_entity_still_before_lead(self):
        # Stage 2 fires before stage 3 when stage 1 passes.
        text = "长江存储 aliyun 存储 存储芯片 能效 产品 价格" * 5
        v = explain_relevance(text, "长鑫 DRAM")
        assert v == RelevanceVerdict.IRRELEVANT_ENTITY

    def test_irrelevant_entity_takes_priority_over_lead(self):
        # Stage 1 passes (density high — 6/7 single-char keywords hit),
        # Stage 2 fails (entity "费马大定理" not present, only similar
        # word "费马大数定理"), Stage 3 would also fail (primary
        # "费马大定理" not in text). Stage 2 verdict wins because
        # it's executed before stage 3.
        text = "费马大数定理 是 数论著名定理，长江存储 是 中国 存储 芯片 巨头"
        v = explain_relevance(text, "费马大定理 长鑫")
        assert v == RelevanceVerdict.IRRELEVANT_ENTITY
