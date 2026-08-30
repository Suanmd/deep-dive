"""Integration tests for the Orchestrator with mocked engines + fetchers.

Validates end-to-end flow without network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deep_dive.crawler.engines.base import SearchEngine, SearchHit
from deep_dive.crawler.fetchers.base import Fetcher
from deep_dive.config import Config
from deep_dive.orchestrator import Orchestrator
from deep_dive.types import FetchStatus, TaskStatus


class MockEngine(SearchEngine):
    """Returns a fixed list of URLs; configurable for quota / failure."""

    name = "mock"

    def __init__(self, urls=None, *, quota=False, fail=False, empty=False):
        super().__init__(timeout_s=1.0)
        self.urls = urls or []
        self.quota = quota
        self.fail = fail
        self.empty = empty

    def _raw_search(self, query, topk):
        if self.fail:
            from deep_dive.crawler.engines.base import SearchEngineError
            raise SearchEngineError("mock failure")
        if self.quota:
            from deep_dive.crawler.engines.base import SearchEngineQuotaError
            raise SearchEngineQuotaError("mock quota")
        if self.empty:
            return []
        return [SearchHit(url=u, title=f"Mock {i}", engine=self.name)
                for i, u in enumerate(self.urls[:topk])]


class MockFetcher(Fetcher):
    """Returns canned HTML for any URL."""

    name = "mock"

    def __init__(self, *, timeout_s=1.0):
        super().__init__(timeout_s=timeout_s)

    def fetch(self, url, *, cookies=None, warmup_url=None):
        # Always return content that passes relevance for "test"
        return (
            "<html><head><title>Test Article</title></head>"
            "<body><p>test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test test</p></body></html>",
            "Test Article",
        )


# ---------------------------------------------------------------------------
# Matrix tests (pure-function)
# ---------------------------------------------------------------------------

class TestOrchestratorE2E:
    def test_full_run_produces_outputs(self, tmp_output_dir):
        engine = MockEngine([
            "https://example.com/a",
            "https://example.com/b",
        ])

        cfg = Config()
        cfg.depth = "quick"
        cfg.output_dir = tmp_output_dir
        cfg.max_workers = 1
        cfg.task_timeout_s = 30
        # Avoid Tavily path
        cfg.tavily_api_key = None
        cfg.tavily_api_key_backup = None

        orch = Orchestrator(
            cfg,
            engines={"mmx": engine},
            fetchers={"primary": MockFetcher, "fallback": MockFetcher},
        )
        result = orch.run(query="test topic")
        assert result.task_results
        assert result.aggregated.total_urls >= 1
        assert result.report_path is not None
        assert result.report_path.exists()

    def test_empty_engine_results_in_no_results(self, tmp_output_dir):
        engine = MockEngine(empty=True)

        cfg = Config()
        cfg.depth = "quick"
        cfg.output_dir = tmp_output_dir
        cfg.tavily_api_key = None
        cfg.tavily_api_key_backup = None

        orch = Orchestrator(
            cfg,
            engines={"mmx": engine},
            fetchers={"primary": MockFetcher, "fallback": MockFetcher},
        )
        result = orch.run(query="test")
        assert all(r.status == TaskStatus.NO_RESULTS for r in result.task_results)

    def test_quota_status_propagated(self, tmp_output_dir):
        engine = MockEngine(quota=True)

        cfg = Config()
        cfg.depth = "quick"
        cfg.output_dir = tmp_output_dir
        cfg.tavily_api_key = None
        cfg.tavily_api_key_backup = None

        orch = Orchestrator(
            cfg,
            engines={"mmx": engine},
            fetchers={"primary": MockFetcher, "fallback": MockFetcher},
        )
        result = orch.run(query="test")
        assert all(r.status == TaskStatus.QUOTA_EXCEEDED for r in result.task_results)
        assert result.global_status == "quota_exceeded"

    def test_summary_json_written(self, tmp_output_dir):
        engine = MockEngine(["https://example.com/a"])

        cfg = Config()
        cfg.depth = "quick"
        cfg.output_dir = tmp_output_dir
        cfg.tavily_api_key = None
        cfg.tavily_api_key_backup = None

        orch = Orchestrator(
            cfg,
            engines={"mmx": engine},
            fetchers={"primary": MockFetcher, "fallback": MockFetcher},
        )
        orch.run(query="test")

        # Find the topic dir that was created
        subdirs = [d for d in tmp_output_dir.iterdir() if d.is_dir()]
        assert len(subdirs) == 1
        summary = subdirs[0] / "summary.json"
        assert summary.exists()
        data = json.loads(summary.read_text(encoding="utf-8"))
        assert data["query"] == "test"
        assert "task_results" in data
