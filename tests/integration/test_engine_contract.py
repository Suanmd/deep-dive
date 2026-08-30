"""Integration tests for the search-engine interface contract.

Validates that all engines satisfy the contract expected by the
orchestrator: ``name``, ``search(query, topk) -> list[SearchHit]``,
plus the quota / error signal conventions.
"""

from __future__ import annotations

import pytest

from deep_dive.crawler.engines import MMXEngine, TavilyEngine, get_engine, register_engine
from deep_dive.crawler.engines.base import (
    SearchEngine,
    SearchEngineError,
    SearchEngineQuotaError,
    SearchEngineTimeoutError,
)
from deep_dive.filters.canonical import canonicalize_url
from deep_dive.types import SearchHit


class _StubEngine(SearchEngine):
    """Test stub that returns a fixed list of URLs."""

    name = "stub"

    def __init__(self, urls_to_return=None, *, quota=False, raise_timeout=False):
        super().__init__(timeout_s=1.0)
        self.urls = urls_to_return or []
        self.quota = quota
        self.raise_timeout = raise_timeout

    def _raw_search(self, query, topk):
        if self.raise_timeout:
            raise SearchEngineTimeoutError("stub timeout")
        if self.quota:
            raise SearchEngineQuotaError("stub quota")
        return [SearchHit(url=u, title=f"Title {i}", engine=self.name)
                for i, u in enumerate(self.urls[:topk])]


class TestEngineRegistry:
    def test_get_mmx(self):
        e = get_engine("mmx")
        assert isinstance(e, MMXEngine)

    def test_get_tavily(self):
        e = get_engine("tavily")
        assert isinstance(e, TavilyEngine)

    def test_case_insensitive(self):
        e = get_engine("MMX")
        assert isinstance(e, MMXEngine)

    def test_unknown_raises(self):
        with pytest.raises(SearchEngineError):
            get_engine("nonexistent")

    def test_register_custom(self):
        register_engine("stub_test", _StubEngine)
        try:
            e = get_engine("stub_test")
            assert isinstance(e, _StubEngine)
        finally:
            # Best-effort cleanup; not strictly required since registry
            # is process-local.
            from deep_dive.crawler.engines import _ENGINE_REGISTRY
            _ENGINE_REGISTRY.pop("stub_test", None)


class TestStubEngineContract:
    def test_returns_search_hits(self):
        e = _StubEngine(["https://example.com/a", "https://example.com/b"])
        hits = e.search("test", 5)
        assert len(hits) == 2
        assert all(isinstance(h, SearchHit) for h in hits)
        assert hits[0].engine == "stub"

    def test_topk_limit(self):
        e = _StubEngine([f"https://example.com/{i}" for i in range(10)])
        hits = e.search("test", 3)
        assert len(hits) == 3

    def test_quota_raises(self):
        e = _StubEngine(quota=True)
        with pytest.raises(SearchEngineQuotaError):
            e.search("test", 5)

    def test_timeout_raises(self):
        e = _StubEngine(raise_timeout=True)
        with pytest.raises(SearchEngineTimeoutError):
            e.search("test", 5)


class TestEngineFiltersOutput:
    def test_smart_filter_applied_to_results(self):
        # Stub returning spam + clean URLs
        urls = [
            "https://example.com/a",  # clean
            "https://doc88.com/123",   # spam → dropped
            "https://example.com/b",  # clean
        ]
        e = _StubEngine(urls)
        hits = e.search("test", 10)
        # Only clean ones should remain
        urls_out = {h.url for h in hits}
        assert "https://example.com/a" in urls_out
        assert "https://example.com/b" in urls_out
        assert not any("doc88.com" in u for u in urls_out)
