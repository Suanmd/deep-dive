"""Tests for the site-targeted → Tavily primary fix.

Background: site-targeted tasks (``站点定向:arxiv.org`` etc.) used to
return 0 URLs because the orchestrator hard-coded
``use_mmx_only = ... or site_targeted``, which forced site-targeted
work to MMX-only. MMX silently ignores the ``site:`` operator and
returns generic hits that all get dropped by the site-targeted
post-filter.

Fix: site-targeted tasks in auto mode now prefer **Tavily as primary**,
because Tavily honours ``site:`` natively. MMX stays as fallback.
User-explicit choices (``--search-engine mmx`` / ``--no-tavily``)
are preserved — they bypass the new auto-prefer-Tavily logic.

These tests exercise :func:`deep_dive.orchestrator._run_one_task` with
mock engines that simulate the failure mode (MMX ignoring ``site:``,
Tavily honouring ``site:``) and assert the engine selection +
site-targeted post-filter behaviour.
"""

from __future__ import annotations

from pathlib import Path

from deep_dive.config import Config
from deep_dive.crawler.engines.base import SearchEngine, SearchEngineQuotaError, SearchHit
from deep_dive.crawler.fetchers.base import Fetcher
from deep_dive.orchestrator import _run_one_task
from deep_dive.types import TaskStatus

# ---------------------------------------------------------------------------
# Mock engines that simulate the real failure mode (MMX ignores site:)
# ---------------------------------------------------------------------------


class _IgnoreSiteEngine(SearchEngine):
    """Simulates MMX: returns generic off-domain hits even with ``site:``
    in the query."""

    name = "mmx"

    def __init__(self, urls: list[str], *, raise_quota: bool = False):
        super().__init__(timeout_s=1.0)
        self.urls = urls
        self.raise_quota = raise_quota
        self.search_calls: list[str] = []  # capture every query we see

    def _raw_search(self, query, topk):
        self.search_calls.append(query)
        if self.raise_quota:
            from deep_dive.crawler.engines.base import SearchEngineQuotaError

            raise SearchEngineQuotaError("simulated quota")
        return [SearchHit(url=u, title=f"mmx-{i}", engine=self.name) for i, u in enumerate(self.urls[:topk])]


class _HonorSiteEngine(SearchEngine):
    """Simulates Tavily: returns hits from the requested target site only.
    This is the behaviour we want for site-targeted tasks: in auto mode
    the orchestrator picks Tavily as primary for site-targeted work."""

    name = "tavily"

    def __init__(self, urls: list[str]):
        super().__init__(timeout_s=1.0)
        self.urls = urls
        self.search_calls: list[str] = []

    def _raw_search(self, query, topk):
        self.search_calls.append(query)
        return [
            SearchHit(url=u, title=f"tavily-{i}", engine=self.name) for i, u in enumerate(self.urls[:topk])
        ]


class _EmptyEngine(SearchEngine):
    """Returns nothing — simulates quota/auth failure recovery attempt."""

    name = "empty"

    def _raw_search(self, query, topk):
        return []


class _StubFetcher(Fetcher):
    """Returns a tiny but valid HTML payload (passes relevance check)."""

    name = "stub"

    def __init__(self, *, timeout_s: float = 1.0):
        super().__init__(timeout_s=timeout_s)

    def fetch(self, url, *, cookies=None, warmup_url=None):
        return (
            "<html><head><title>T</title></head>"
            "<body><p>test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test</p></body></html>",
            "T",
        )


def _make_config(tmp_path: Path, **overrides) -> Config:
    cfg = Config()
    cfg.depth = "quick"
    cfg.output_dir = tmp_path
    cfg.max_workers = 1
    cfg.task_timeout_s = 30
    cfg.tavily_api_key = None
    cfg.tavily_api_key_backup = None
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# The main bug: site-targeted should pick Tavily (which honors site:),
# not MMX (which ignores site: and returns garbage that all get filtered).
# ---------------------------------------------------------------------------


class TestSiteTargetedPrefersTavily:
    """Site-targeted tasks in auto mode use Tavily as primary engine."""

    def test_site_targeted_uses_tavily_primary_when_available(self, tmp_path):
        """Failure mode: MMX returns 9 off-site hits (wrong domain),
        Tavily returns 5 on-site hits (correct domain). The task must
        succeed using Tavily, not fail with MMX's off-site garbage."""
        mmx_garbage = _IgnoreSiteEngine([f"https://wrong-domain.com/{i}" for i in range(9)])
        tavily_correct = _HonorSiteEngine(
            [
                "https://arxiv.org/abs/1706.03762",
                "https://arxiv.org/abs/2106.05237",
                "https://arxiv.org/abs/2005.14165",
                "https://arxiv.org/abs/1409.3215",
                "https://arxiv.org/abs/1409.0473",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:arxiv.org",
            query="Attention Is All You Need site:arxiv.org",
            topk=12,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_garbage, "tavily": tavily_correct},
            config=cfg,
            cookies_map={},
            main_query="test",  # match _StubFetcher HTML for relevance
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # SUCCESS — the bug (NO_RESULTS with site_filtered_out) is fixed.
        assert result.status == TaskStatus.SUCCESS, (
            f"expected SUCCESS (Tavily primary), got {result.status}: "
            f"engine={result.extra.get('engine')}, "
            f"site_filtered_out={result.extra.get('site_filtered_out')}, "
            f"raw_hit_count={result.extra.get('raw_hit_count')}"
        )
        # The primary engine that fired was Tavily (not MMX).
        assert result.extra["engine"] == "tavily", (
            f"site-targeted primary must be Tavily (got {result.extra['engine']})"
        )
        # Tavily got the site: query first (i.e., was primary, not fallback).
        assert any("site:arxiv.org" in q for q in tavily_correct.search_calls), (
            "Tavily must be called with the site: query as primary"
        )
        # MMX is allowed as fallback when Tavily returns < topk (this is
        # the normal fallback chain behaviour). The post-filter drops MMX's
        # off-site garbage; Tavily's arxiv hits survive. The KEY assertion
        # is that the task SUCCEEDED — pre-fix this was NO_RESULTS.
        assert result.url_count >= 5, (
            f"expected ≥5 successful fetches (Tavily's arxiv hits), got {result.url_count}"
        )

    def test_site_targeted_mm_x_fallback_when_tavily_quota(self, tmp_path):
        """If Tavily hits quota, MMX becomes primary.

        Even though MMX ignores ``site:``, the existing post-filter and
        fallback machinery still try to recover. The user gets some URLs
        rather than nothing.
        """
        mmx_garbage = _IgnoreSiteEngine(
            [
                "https://transformers-wrong-domain.com/a",
                "https://transformers-wrong-domain.com/b",
            ]
        )
        # (legacy) quota_tavily = _HonorSiteEngine([])  # noqa: F841 — removed

        class _QuotaTavily(SearchEngine):
            name = "tavily"

            def _raw_search(self, query, topk):
                raise SearchEngineQuotaError("simulated Tavily quota")

        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:arxiv.org",
            query="Attention Is All You Need site:arxiv.org",
            topk=12,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_garbage, "tavily": _QuotaTavily()},
            config=cfg,
            cookies_map={},
            main_query="Attention Is All You Need",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # Even with Tavily quota, degradation to MMX kept the task alive.
        # The exact outcome depends on site_fallback_used / DDG — but we
        # MUST NOT have crashed or returned without trying fallback.
        assert result.extra.get("degraded_to") == "mmx", (
            f"Tavily quota must degrade to MMX (got degraded_to={result.extra.get('degraded_to')})"
        )
        # MMX was called as fallback.
        assert len(mmx_garbage.search_calls) >= 1, "MMX must be called as fallback after Tavily quota"

    def test_explicit_mm_x_preserves_user_intent_for_site_targeted(self, tmp_path):
        """``--search-engine mmx`` still routes site-targeted to MMX.

        The auto-prefer-Tavily fix only kicks in for auto mode.
        User-explicit choices are preserved (regression guard for
        power users).
        """
        mmx_garbage = _IgnoreSiteEngine(
            [
                "https://wrong-domain.com/a",
            ]
        )
        tavily_correct = _HonorSiteEngine(
            [
                "https://arxiv.org/abs/1706.03762",
            ]
        )
        cfg = _make_config(tmp_path, search_engine="mmx")  # user explicit choice
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:arxiv.org",
            query="Attention Is All You Need site:arxiv.org",
            topk=12,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_garbage, "tavily": tavily_correct},
            config=cfg,
            cookies_map={},
            main_query="Attention Is All You Need",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # MMX was primary (user explicit); Tavily was NOT consulted.
        assert result.extra["engine"] == "mmx"
        assert tavily_correct.search_calls == [], "Tavily must NOT be called when user explicitly chose MMX"

    def test_no_tavily_preserves_user_intent_for_site_targeted(self, tmp_path):
        """``--no-tavily`` still routes site-targeted to MMX only.

        Regression guard: when the user disables Tavily, the
        auto-prefer-Tavily fix must not silently re-enable it.
        """
        mmx_garbage = _IgnoreSiteEngine(
            [
                "https://wrong-domain.com/a",
            ]
        )
        cfg = _make_config(tmp_path, no_tavily=True)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:arxiv.org",
            query="Attention Is All You Need site:arxiv.org",
            topk=12,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_garbage, "tavily": _HonorSiteEngine([])},
            config=cfg,
            cookies_map={},
            main_query="Attention Is All You Need",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # MMX was primary; Tavily was NOT consulted.
        assert result.extra["engine"] == "mmx"
        # No fallback to Tavily.
        assert result.extra.get("fallback_used") is False

    def test_non_site_targeted_uses_mmx_primary_regression(self, tmp_path):
        """NON-site-targeted tasks still use MMX as primary.

        The auto-prefer-Tavily fix is scoped to site-targeted only.
        General tasks (``中文原始``, ``英文基础``, etc.) must keep
        using the MMX → Tavily fallback chain.
        """
        mmx_partial = _IgnoreSiteEngine([f"https://example.com/{i}" for i in range(3)])
        tavily_supplement = _HonorSiteEngine([f"https://example.com/{i}" for i in range(3, 10)])
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="中文原始",  # NOT site-targeted
            query="test",
            topk=10,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_partial, "tavily": tavily_supplement},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.SUCCESS
        # MMX primary, Tavily fallback (current behaviour preserved for backward-compat).
        assert result.extra["engine"] == "mmx"
        assert result.extra["fallback_used"] is True

    def test_site_targeted_no_tavily_available_still_fails(self, tmp_path):
        """If Tavily is NOT in engines dict (not installed), site-targeted
        falls back to MMX (per the current routing rule). The task will likely fail
        because MMX ignores site:, but at least we tried MMX."""
        mmx_garbage = _IgnoreSiteEngine(
            [
                "https://wrong-domain.com/a",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:arxiv.org",
            query="Attention Is All You Need site:arxiv.org",
            topk=12,
            exclude=(),
        )
        # Note: NO "tavily" key in engines dict.
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mmx_garbage},
            config=cfg,
            cookies_map={},
            main_query="Attention Is All You Need",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # MMX was used (only option). Hits were off-site → NO_RESULTS.
        # This is the current "no Tavily = MMX only" behaviour, preserved.
        assert result.status == TaskStatus.NO_RESULTS
        assert result.extra.get("engine") == "mmx"
