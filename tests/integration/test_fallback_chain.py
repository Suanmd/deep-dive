"""Tests for the mmx → Tavily **fallback chain** in :func:`_run_one_task`.

When mmx returns fewer URLs than ``topk``, Tavily is automatically
called with the remainder to fill the gap (mirroring the legacy
``search_urls()`` behaviour that was lost when engines were
refactored into separate ``SearchEngine`` classes).

These tests exercise :func:`deep_dive.orchestrator._run_one_task`
directly with two mock engines (mmx-style + Tavily-style) so we can
verify each branch in isolation.
"""

from __future__ import annotations

from pathlib import Path

from deep_dive.config import Config
from deep_dive.crawler.engines.base import SearchEngine, SearchEngineQuotaError, SearchHit
from deep_dive.crawler.fetchers.base import Fetcher
from deep_dive.orchestrator import _run_one_task
from deep_dive.types import TaskStatus


class _PartialEngine(SearchEngine):
    """Mock engine returning a fixed number of URLs (``primary`` role)."""

    name = "partial"

    def __init__(self, urls: list[str], *, raise_quota: bool = False):
        super().__init__(timeout_s=1.0)
        self.urls = urls
        self.raise_quota = raise_quota

    def _raw_search(self, query, topk):
        if self.raise_quota:
            raise SearchEngineQuotaError("simulated quota")
        return [SearchHit(url=u, title=f"hit-{i}", engine=self.name) for i, u in enumerate(self.urls[:topk])]


class _SupplementalEngine(SearchEngine):
    """Mock engine returning more URLs on demand (``fallback`` role)."""

    name = "supplemental"

    def __init__(self, urls: list[str]):
        super().__init__(timeout_s=1.0)
        self.urls = urls

    def _raw_search(self, query, topk):
        return [SearchHit(url=u, title=f"supp-{i}", engine=self.name) for i, u in enumerate(self.urls[:topk])]


class _FailEngine(SearchEngine):
    """Mock engine that raises a generic SearchEngineError."""

    name = "failer"

    def _raw_search(self, query, topk):
        from deep_dive.crawler.engines.base import SearchEngineError

        raise SearchEngineError("simulated failure")


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


def _make_config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.depth = "quick"
    cfg.output_dir = tmp_path
    cfg.max_workers = 1
    cfg.task_timeout_s = 30
    cfg.tavily_api_key = None
    cfg.tavily_api_key_backup = None
    return cfg


# ---------------------------------------------------------------------------
# Fallback chain behaviour
# ---------------------------------------------------------------------------


class TestFallbackChainTriggered:
    """mmx returns fewer than ``topk`` URLs → Tavily fills the gap."""

    def test_partial_mm_x_triggers_tavily(self, tmp_path):
        """mmx returns 3 of 10 URLs → Tavily adds 8 more."""
        partial = _PartialEngine(
            [
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/c",
            ]
        )
        supplemental = _SupplementalEngine(
            [
                "https://example.com/d",
                "https://example.com/e",
                "https://example.com/f",
                "https://example.com/g",
                "https://example.com/h",
                "https://example.com/i",
                "https://example.com/j",
                "https://example.com/k",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="test task",
            query="test",
            topk=10,
            exclude=(),
        )
        base_dir = tmp_path / "raw"
        result = _run_one_task(
            row,
            base_dir=base_dir,
            engines={"mmx": partial, "tavily": supplemental},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.SUCCESS
        assert result.extra["fallback_used"] is True
        assert result.extra["engine"] == "partial"
        assert result.extra["fallback_status"] == "ok"
        assert result.url_count >= 8  # primary 3 + supplemental 8 (deduped)

    def test_sufficient_mm_x_skips_tavily(self, tmp_path):
        """mmx returns >= topk → Tavily is NOT consulted."""
        partial = _PartialEngine([f"https://example.com/{i}" for i in range(10)])
        # supplemental would add noise if called — make it return something
        # obviously wrong to detect if it was invoked.
        sentinel_called = []

        class _SpyEngine(SearchEngine):
            name = "spy"

            def _raw_search(self, query, topk):
                sentinel_called.append(True)
                return [SearchHit(url="https://should-not-appear/", title="SENTINEL", engine=self.name)]

        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": partial, "tavily": _SpyEngine()},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.SUCCESS
        assert result.extra["fallback_used"] is False
        assert sentinel_called == []

    def test_quota_on_mm_x_escalates_to_tavily_as_primary(self, tmp_path):
        """mmx hits quota → Tavily is promoted to primary.

        Different providers have independent quotas, so a Tavily key
        can recover a run that mmx couldn't complete. The
        engine-degradation step explicitly reverses the legacy
        assumption of shared quota.
        """
        quota_engine = _PartialEngine([], raise_quota=True)
        supplemental = _SupplementalEngine([f"https://example.com/{i}" for i in range(5)])
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": quota_engine, "tavily": supplemental},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # Tavily degraded-to success → task succeeds via the recovered engine.
        assert result.status == TaskStatus.SUCCESS
        assert result.extra["degraded_to"] == "supplemental"
        assert result.extra["fallback_used"] is True
        assert result.extra["fallback_status"] == "ok"
        assert result.url_count >= 5

    def test_both_engines_quota_returns_quota_exceeded(self, tmp_path):
        """When both mmx AND Tavily hit quota, the task returns QUOTA."""
        quota_engine = _PartialEngine([], raise_quota=True)

        class _QuotaSpy(SearchEngine):
            name = "quota_spy"

            def _raw_search(self, query, topk):
                raise SearchEngineQuotaError("simulated quota")

        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": quota_engine, "tavily": _QuotaSpy()},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.QUOTA_EXCEEDED
        # degraded_to records the attempted secondary engine so the audit
        # log shows we tried Tavily too.
        assert result.extra["degraded_to"] == "quota_spy"

    def test_quota_degradation_skipped_when_no_tavily(self, tmp_path):
        """--no-tavily: mmx quota → no degradation attempt (no fallback available)."""
        quota_engine = _PartialEngine([], raise_quota=True)
        cfg = _make_config(tmp_path)
        cfg.no_tavily = True
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": quota_engine},  # no tavily
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.QUOTA_EXCEEDED
        assert result.extra.get("degraded_to") is None

    def test_quota_degradation_skipped_for_explicit_engine(self, tmp_path):
        """--search-engine mmx: mmx quota → no degradation (user chose mmx)."""
        quota_engine = _PartialEngine([], raise_quota=True)
        cfg = _make_config(tmp_path)
        cfg.search_engine = "mmx"
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": quota_engine, "tavily": _SupplementalEngine([])},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.QUOTA_EXCEEDED
        assert result.extra.get("degraded_to") is None

    def test_tavily_failure_does_not_propagate(self, tmp_path):
        """mmx partial, Tavily raises → task still succeeds with partial results;
        ``fallback_used`` is True (we attempted the fallback) and
        ``fallback_status`` is "failed"."""
        partial = _PartialEngine([f"https://example.com/{i}" for i in range(3)])
        broken_tavily = _FailEngine()
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": partial, "tavily": broken_tavily},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        # mmx returned 3 hits, pipeline should have succeeded with 3
        assert result.status == TaskStatus.SUCCESS
        assert result.extra["fallback_used"] is True
        assert result.extra["fallback_status"] == "failed"

    def test_empty_result_without_fallback(self, tmp_path):
        """No engines → empty hits, status=NO_RESULTS, output_dir present."""
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={},  # empty!
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.NO_RESULTS
        assert result.output_dir is not None
        assert result.output_dir.exists()


class TestEngineSelection:
    """Engine selection respects --search-engine and --no-tavily flags."""

    def test_explicit_tavily_skips_mmx(self, tmp_path):
        """--search-engine tavily → mmx NOT used at all."""
        mmx_called = []

        class _MmxSpy(SearchEngine):
            name = "mmx"

            def _raw_search(self, query, topk):
                mmx_called.append(True)
                return [SearchHit(url="https://wrong/", title="WRONG", engine=self.name)]

        supplemental = _SupplementalEngine([f"https://example.com/{i}" for i in range(5)])
        cfg = _make_config(tmp_path)
        cfg.search_engine = "tavily"
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": _MmxSpy(), "tavily": supplemental},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert mmx_called == []
        assert result.extra["engine"] == "supplemental"

    def test_explicit_mmx_skips_tavily(self, tmp_path):
        """--search-engine mmx → no fallback even when partial."""
        partial = _PartialEngine([f"https://example.com/{i}" for i in range(3)])
        tavily_called = []

        class _TavilySpy(SearchEngine):
            name = "tavily"

            def _raw_search(self, query, topk):
                tavily_called.append(True)
                return [SearchHit(url="https://wrong/", title="WRONG", engine=self.name)]

        cfg = _make_config(tmp_path)
        cfg.search_engine = "mmx"
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": partial, "tavily": _TavilySpy()},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert tavily_called == []
        assert result.extra["fallback_used"] is False

    def test_no_tavily_flag_prevents_fallback(self, tmp_path):
        """--no-tavily → mmx partial, no Tavily fallback."""
        partial = _PartialEngine([f"https://example.com/{i}" for i in range(3)])
        tavily_called = []

        class _TavilySpy(SearchEngine):
            name = "tavily"

            def _raw_search(self, query, topk):
                tavily_called.append(True)
                return []

        cfg = _make_config(tmp_path)
        cfg.no_tavily = True
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="test", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": partial, "tavily": _TavilySpy()},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert tavily_called == []
        assert result.extra["fallback_used"] is False


class TestOutputDirPersistence:
    """``task_dir`` is created BEFORE the engine call so debug
    artifacts survive engine failures."""

    def test_task_dir_created_when_mmx_quota(self, tmp_path):
        """Engine raises quota → task_dir still exists for debug inspection."""
        quota = _PartialEngine([], raise_quota=True)
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="quota_task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": quota},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.output_dir is not None
        assert result.output_dir.exists()
        assert result.output_dir.name == "raw"

    def test_task_dir_created_when_no_engines(self, tmp_path):
        """No engines available → task_dir still exists."""
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(note="no_engine_task", query="test", topk=10, exclude=())
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.output_dir is not None
        assert result.output_dir.exists()


class TestSiteTargetedPostFilter:
    """Site-targeted post-filter.

    Some sites (e.g. ``stackoverflow.com``) have no content for
    China-native concepts. Even with a ``site:`` filter in the query,
    engines sometimes return off-site noise. The post-filter step:
      - keeps only hits whose URL contains the target site
      - if 0 hits remain → ``NO_RESULTS`` with ``site_filtered_out=True``
      - if some hits remain → keep them, mark ``site_filtered_out=True``

    In auto mode, site-targeted tasks now use Tavily as primary (because
    Tavily honours ``site:`` natively; see
    ``TestSiteTargetedPrefersTavily`` below). The realistic post-filter
    test therefore provides both engines and verifies the actual
    behaviour under the current engine routing.
    """

    def test_all_off_site_hits_marked_no_results(self, tmp_path):
        """All hits are off-site → ``NO_RESULTS`` + ``site_filtered_out`` flag.

        In auto mode, site-targeted tasks use Tavily primary with MMX
        as fallback. We provide both engines here so the post-filter
        block is actually exercised (Tavily returns off-site hits).
        """
        # Both engines return off-site hits — post-filter drops all of them.
        off_site_tavily = _SupplementalEngine(
            [
                "https://wrong-domain.com/a",
                "https://other-domain.com/b",
            ]
        )
        off_site_mmx = _PartialEngine(
            [
                "https://wrong-domain.com/x",
                "https://other-domain.com/y",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:stackoverflow.com",
            query="test",
            topk=10,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": off_site_mmx, "tavily": off_site_tavily},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.NO_RESULTS
        assert result.extra.get("site_filtered_out") is True
        assert result.extra.get("target_site") == "stackoverflow.com"
        # raw_hit_count records the primary-engine's pre-filter count.
        assert result.extra.get("raw_hit_count") == 4

    def test_no_tavily_site_targeted_preserves_mm_x_only_routing(self, tmp_path):
        """``--no-tavily`` still routes site-targeted to MMX only.

        Regression guard: preserve the user's explicit intent when they
        disable Tavily.
        """
        off_site = _PartialEngine(
            [
                "https://wrong-domain.com/a",
                "https://other-domain.com/b",
            ]
        )
        cfg = _make_config(tmp_path)
        cfg.no_tavily = True  # user disabled Tavily
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:stackoverflow.com",
            query="test",
            topk=10,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": off_site},  # no tavily
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.NO_RESULTS
        assert result.extra.get("site_filtered_out") is True
        assert result.extra.get("engine") == "partial"  # MMX was primary
        assert result.extra.get("target_site") == "stackoverflow.com"

    def test_partial_off_site_keeps_in_site_hits(self, tmp_path):
        """Mixed hits: off-site dropped, in-site kept."""
        mixed = _PartialEngine(
            [
                "https://stackoverflow.com/q/123",
                "https://wrong-domain.com/a",
                "https://stackoverflow.com/q/456",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:stackoverflow.com",
            query="test",
            topk=10,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": mixed},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.SUCCESS
        assert result.extra.get("site_filtered_out") is True
        # Only the 2 stackoverflow.com hits should be in the pipeline.
        assert result.url_count >= 2

    def test_all_in_site_hits_unchanged(self, tmp_path):
        """All hits on target → no filtering, normal success."""
        on_site = _PartialEngine(
            [
                "https://stackoverflow.com/q/123",
                "https://stackoverflow.com/q/456",
            ]
        )
        cfg = _make_config(tmp_path)
        from deep_dive.orchestrator import MatrixRow

        row = MatrixRow(
            note="站点定向:stackoverflow.com",
            query="test",
            topk=10,
            exclude=(),
        )
        result = _run_one_task(
            row,
            base_dir=tmp_path / "raw",
            engines={"mmx": on_site},
            config=cfg,
            cookies_map={},
            main_query="test",
            fetcher_classes={"primary": _StubFetcher, "fallback": _StubFetcher},
        )
        assert result.status == TaskStatus.SUCCESS
        assert result.extra.get("site_filtered_out") is False
