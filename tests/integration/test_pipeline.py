"""Integration tests for the crawl pipeline.

Uses a stub fetcher to exercise the full pipeline without network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deep_dive.crawler.fetchers.base import Fetcher
from deep_dive.crawler.pipeline import CrawlPipeline, PipelineConfig
from deep_dive.types import FetchStatus


class StubFetcher(Fetcher):
    """Returns canned HTML for a list of URLs."""

    name = "stub"

    def __init__(self, url_to_response: dict[str, tuple[str, str]] | None = None,
                 block_urls: set[str] | None = None,
                 fail_urls: set[str] | None = None):
        super().__init__(timeout_s=1.0)
        self.url_to_response = url_to_response or {}
        self.block_urls = block_urls or set()
        self.fail_urls = fail_urls or set()

    def fetch(self, url, *, cookies=None, warmup_url=None):
        if url in self.fail_urls:
            raise RuntimeError("simulated failure")
        if url in self.block_urls:
            return ("<html>just a moment... verify you are human</html>", "")
        return self.url_to_response.get(url, ("<html><body></body></html>", "Default"))


SAMPLE_HTML = """<!doctype html>
<html>
<head><title>Sample Article Title</title></head>
<body>
<article>
<h1>Sample Article</h1>
<p>This is the main content of the article. It is a long enough paragraph
that the extractor will return something useful. We discuss several
topics relevant to our search query about technology and innovation.</p>
<p>Continued main content here with more detail and context.</p>
</article>
</body>
</html>
"""


class TestPipelineBasics:
    def test_empty_url_list_returns_empty(self, tmp_output_dir):
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="test")
        pipeline = CrawlPipeline(cfg, primary_fetcher=StubFetcher())
        results = pipeline.run([], source_task="test")
        assert results == []

    def test_successful_fetch(self, tmp_output_dir):
        fetcher = StubFetcher({
            "https://example.com/a": (SAMPLE_HTML, "Sample Article Title"),
        })
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="article", enable_relevance_check=False)
        # Override relevance check off (text doesn't have many query matches)
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="article technology", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        results = pipeline.run(["https://example.com/a"], source_task="test")
        assert len(results) == 1
        assert results[0].status == FetchStatus.SUCCESS
        assert results[0].chars > 0

    def test_block_page_marked_blocked(self, tmp_output_dir):
        fetcher = StubFetcher(
            {"https://example.com/blocked": ("<html>just a moment... verify you are human</html>", "")},
        )
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="test", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        results = pipeline.run(["https://example.com/blocked"], source_task="test")
        assert results[0].status == FetchStatus.BLOCKED

    def test_failure_marked_failed(self, tmp_output_dir):
        fetcher = StubFetcher(fail_urls={"https://example.com/fail"})
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="test", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        results = pipeline.run(["https://example.com/fail"], source_task="test")
        assert results[0].status == FetchStatus.FAILED

    def test_metadata_persisted(self, tmp_output_dir):
        fetcher = StubFetcher({
            "https://example.com/a": (SAMPLE_HTML, "Sample"),
        })
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="sample", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        pipeline.run(["https://example.com/a"], source_task="test_task")
        metadata_path = tmp_output_dir / "metadata.json"
        assert metadata_path.exists()

    def test_url_mapping_persisted(self, tmp_output_dir):
        fetcher = StubFetcher({
            "https://example.com/a": (SAMPLE_HTML, "Sample"),
        })
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="sample", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        pipeline.run(["https://example.com/a"], source_task="test_task")
        url_mapping_path = tmp_output_dir / "url_mapping.json"
        assert url_mapping_path.exists()


class TestPipelineRelevance:
    def test_relevance_check_drops_irrelevant(self, tmp_output_dir):
        # Article with NO overlap to query
        fetcher = StubFetcher({
            "https://example.com/irrelevant": (SAMPLE_HTML, "Random Article"),
        })
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="黄金投资", enable_relevance_check=True)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        results = pipeline.run(["https://example.com/irrelevant"], source_task="test")
        assert results[0].status == FetchStatus.IRRELEVANT

    def test_relevance_check_disabled(self, tmp_output_dir):
        fetcher = StubFetcher({
            "https://example.com/anything": (SAMPLE_HTML, "Random"),
        })
        cfg = PipelineConfig(output_dir=tmp_output_dir, main_query="黄金投资", enable_relevance_check=False)
        pipeline = CrawlPipeline(cfg, primary_fetcher=fetcher)
        results = pipeline.run(["https://example.com/anything"], source_task="test")
        assert results[0].status == FetchStatus.SUCCESS
