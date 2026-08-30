"""Tests for the site-targeted English-baseline fix.

Background: ``site:en_domain`` + a Chinese query returns 0 results
because most English-first domains (``lmsys.org``, ``arxiv.org``,
``github.com``, ``huggingface.co``) are not indexed for Chinese
content.

Fix: ``build_search_matrix_from_plan`` (and the legacy matrix builder)
now use ``plan.english_search_terms[0]`` for site-targeted queries
when the plan has English search terms, falling back to
``plan.query`` only when ``language_priority == "zh-only"`` or no
English terms exist.
"""

from __future__ import annotations

import dataclasses

import pytest

from deep_dive.config import Config, load_config
from deep_dive.orchestrator import build_search_matrix_from_plan
from deep_dive.types import ResearchPlan


def _plan(
    query: str = "大模型 天梯榜 2026",
    english_search_terms: list[str] | None = None,
    language_priority: str = "balanced",
    target_sites: list[str] | None = None,
    variants: dict[str, str] | None = None,
    kind: str = "tech",
) -> ResearchPlan:
    return ResearchPlan(
        query=query,
        kind=kind,
        depth="normal",
        language_priority=language_priority,
        english_search_terms=english_search_terms or [
            "LLM leaderboard 2026 Chatbot Arena LMSYS",
            "Open LLM Leaderboard Hugging Face 2026",
        ],
        variants=variants or {
            "refined": "大模型 天梯榜 2026 LLM 排名 评测 基准",
            "critique": "大模型 天梯榜 2026 局限性 不可靠 排名",
            "academic": "LLM 大模型 评测 基准 benchmark",
            "primary": "Chatbot Arena LMSYS Open LLM Leaderboard 官方",
            "comparative": "大模型 天梯榜 vs Chatbot Arena",
        },
        target_sites=target_sites or ["lmsys.org", "huggingface.co"],
        relevance_threshold=0.3,
        rationale="test plan",
    )


class TestSiteTargetedEnglishBaseline:
    """Site-targeted tasks must use the English baseline query, not the
    original Chinese query."""

    def test_chinese_query_uses_english_for_site_task(self):
        plan = _plan(query="大模型 天梯榜 2026")
        cfg = Config()
        rows, _dropped = build_search_matrix_from_plan(plan, config=cfg)

        site_rows = [r for r in rows if r.note.startswith("站点定向:")]
        assert len(site_rows) >= 2, "expected at least 2 site-targeted rows"

        for row in site_rows:
            site = row.note.split(":", 1)[1].strip()
            # Each site-targeted query must end with the English baseline
            # followed by site:domain, NOT the Chinese query.
            assert row.query.endswith(f"site:{site}"), (
                f"site-targeted query missing site: prefix: {row.query!r}"
            )
            assert "大模型" not in row.query, (
                f"site-targeted query still contains Chinese: {row.query!r} "
                f"(site:lmsys.org with a Chinese query returns 0 hits)"
            )
            assert "LLM" in row.query, (
                f"site-targeted query missing English baseline: {row.query!r}"
            )

    def test_zh_only_plan_falls_back_to_chinese_query(self):
        plan = _plan(
            query="大模型 天梯榜 2026",
            language_priority="zh-only",
            english_search_terms=[],  # no English terms
        )
        cfg = Config()
        rows, _dropped = build_search_matrix_from_plan(plan, config=cfg)

        # zh-only + no English: site-targeted uses original Chinese query
        # (no fallback possible).
        site_rows = [r for r in rows if r.note.startswith("站点定向:")]
        for row in site_rows:
            assert "大模型" in row.query, (
                f"zh-only plan should use Chinese query for site-targeted: "
                f"{row.query!r}"
            )

    def test_en_only_plan_uses_english_query(self):
        plan = _plan(
            query="LLM leaderboard 2026",
            language_priority="en-only",
            english_search_terms=["LLM leaderboard 2026 Chatbot Arena"],
        )
        cfg = Config()
        rows, _dropped = build_search_matrix_from_plan(plan, config=cfg)

        site_rows = [r for r in rows if r.note.startswith("站点定向:")]
        assert len(site_rows) >= 1
        for row in site_rows:
            assert "LLM leaderboard" in row.query

    def test_chinese_plan_with_english_terms_uses_english(self):
        """The bug case: Chinese query + has English terms + balanced lang.

        This is the exact scenario from the user's LLM leaderboard run.
        The fix must use the English baseline.
        """
        plan = _plan(query="大模型 天梯榜 2026", language_priority="balanced")
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        site_row = next(r for r in rows if r.note.startswith("站点定向:lmsys.org"))
        assert "site:lmsys.org" in site_row.query
        # Original Chinese query NOT used for site-targeted
        assert "大模型" not in site_row.query
        # First English term IS used
        assert site_row.query.startswith("LLM leaderboard 2026 Chatbot Arena LMSYS")


class TestSiteTargetedDoesNotBreakNonSiteTasks:
    """The fix must not change non-site-targeted tasks."""

    def test_chinese_original_task_unchanged(self):
        plan = _plan()
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        orig = next(r for r in rows if r.note == "中文原始")
        assert orig.query == "大模型 天梯榜 2026"

    def test_english_baseline_task_unchanged(self):
        plan = _plan()
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        en = next(r for r in rows if r.note == "英文基础")
        assert en.query == "LLM leaderboard 2026 Chatbot Arena LMSYS"


class TestSiteTargetedCrossCapSurvives:
    """When the plan is variant-heavy and the cap is tight, site-targeted
    must still keep its English query (priority 1)."""

    def test_site_targeted_survives_when_variants_overflow(self):
        # 5 variants + 3 english + 2 sites = 10 candidates. Normal depth
        # cap=8. So 2 candidates get dropped. Site-targeted (priority 1)
        # must survive.
        plan = _plan()
        cfg = Config()
        rows, dropped = build_search_matrix_from_plan(plan, config=cfg)
        site_rows = [r for r in rows if r.note.startswith("站点定向:")]
        assert len(site_rows) == 2, (
            f"expected 2 site-targeted rows after cap, got {len(site_rows)}. "
            f"Dropped: {dropped}"
        )
        # And they must still use English query, not Chinese.
        for row in site_rows:
            assert "大模型" not in row.query


class TestSiteTargetedPerSiteSelection:
    """Each site-targeted task uses the ``english_search_terms`` entry
    most relevant to its specific site (substring-match heuristic,
    no hardcoded alias table).

    Previously every site used ``english_search_terms[0]``,
    wasting the specialised terms further down the list when
    ``target_sites`` had domain-specific vocab (e.g.
    ``huggingface.co`` should match ``Hugging Face`` rather than
    ``LMSYS``). :func:`pick_site_query` now routes each site to its
    best-matching English term; sites with no substring overlap fall
    back to the
    caller-supplied fallback (english_search_terms[0] by default).
    """

    def test_lmsys_picks_baseline_huggingface_picks_variant(self):
        """Two sites, each gets a different english term."""
        plan = _plan(
            target_sites=["lmsys.org", "huggingface.co"],
            english_search_terms=[
                "LLM leaderboard 2026 Chatbot Arena LMSYS",
                "Open LLM Leaderboard Hugging Face 2026",
            ],
        )
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)

        lmsys_row = next(r for r in rows if r.note.startswith("站点定向:lmsys.org"))
        hf_row = next(r for r in rows if r.note.startswith("站点定向:huggingface.co"))

        # lmsys → terms[0] (contains LMSYS)
        assert lmsys_row.query.startswith(
            "LLM leaderboard 2026 Chatbot Arena LMSYS site:lmsys.org"
        )
        # huggingface → terms[1] (contains hugging/face), NOT the baseline
        assert hf_row.query.startswith(
            "Open LLM Leaderboard Hugging Face 2026 site:huggingface.co"
        )
        # Critical: huggingface must NOT use the lmsys baseline anymore
        assert "LMSYS" not in hf_row.query
        assert "Chatbot Arena" not in hf_row.query

    def test_three_sites_three_distinct_terms(self):
        """Each site picks its own best-matching term — no overlap.

        Uses depth='full' so n_site=3 (all three sites survive the cap).
        At normal depth n_site=2 and the third site is dropped — that's
        a separate concern covered by ``TestSiteTargetedCrossCapSurvives``.

        Note: ``build_search_matrix_from_plan`` reads ``config.depth``
        (not ``plan.depth``) for n_site calculation, so the test must
        override BOTH plan and config depth.
        """
        plan = _plan(
            target_sites=["lmsys.org", "huggingface.co", "arxiv.org"],
            english_search_terms=[
                "LLM leaderboard 2026 Chatbot Arena LMSYS",
                "Open LLM Leaderboard Hugging Face 2026",
                "arxiv cs.LG 2026 latest LLM papers",
            ],
        )
        plan = ResearchPlan(
            query=plan.query, kind=plan.kind, depth="full",
            language_priority=plan.language_priority,
            english_search_terms=plan.english_search_terms,
            variants=plan.variants, target_sites=plan.target_sites,
            relevance_threshold=plan.relevance_threshold,
            rationale=plan.rationale,
        )
        cfg = dataclasses.replace(Config(), depth="full")
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        site_queries = {r.note: r.query for r in rows if r.note.startswith("站点定向:")}

        assert "站点定向:lmsys.org" in site_queries
        assert "站点定向:huggingface.co" in site_queries
        assert "站点定向:arxiv.org" in site_queries

        # All three should route to a different english term.
        prefixes = {q.split(" site:")[0] for q in site_queries.values()}
        assert len(prefixes) == 3, (
            f"expected 3 distinct english prefixes, got {prefixes}"
        )

    def test_site_with_no_match_falls_back_to_baseline(self):
        """A site whose tokens don't appear in any English term falls
        back to ``english_search_terms[0]`` (always use an English
        query for site-targeted tasks)."""
        plan = _plan(
            target_sites=["zhihu.com"],
            english_search_terms=[
                "LLM leaderboard 2026 Chatbot Arena LMSYS",
                "Open LLM Leaderboard Hugging Face 2026",
            ],
        )
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        zhihu_row = next(r for r in rows if r.note.startswith("站点定向:zhihu.com"))
        # No substring overlap → fallback (terms[0]) → still English, not Chinese
        assert "大模型" not in zhihu_row.query
        assert zhihu_row.query.startswith("LLM leaderboard 2026 Chatbot Arena LMSYS")
        assert "site:zhihu.com" in zhihu_row.query

    def test_zh_only_plan_still_uses_chinese_query(self):
        """zh-only plans (no english_search_terms) keep the original
        Chinese query for site-targeted tasks — fallback to plan.query."""
        plan = _plan(
            query="知乎 热门话题 2026",
            language_priority="zh-only",
            english_search_terms=[],
            target_sites=["zhihu.com"],
        )
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        site_row = next(r for r in rows if r.note.startswith("站点定向:zhihu.com"))
        assert "知乎" in site_row.query
        assert "site:zhihu.com" in site_row.query

    def test_existing_lmsys_baseline_preserved(self):
        """Regression guard: lmsys.org still uses terms[0] (no
        regression — the LMSYS token is still in the baseline)."""
        plan = _plan()  # default english_search_terms[0] contains "LMSYS"
        cfg = Config()
        rows, _ = build_search_matrix_from_plan(plan, config=cfg)
        lmsys_row = next(r for r in rows if r.note.startswith("站点定向:lmsys.org"))
        # Pre-fix assertion still holds.
        assert lmsys_row.query.startswith("LLM leaderboard 2026 Chatbot Arena LMSYS")
        assert "site:lmsys.org" in lmsys_row.query
