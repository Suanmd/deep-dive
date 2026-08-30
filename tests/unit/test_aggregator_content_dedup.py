"""Tests for content-fingerprint deduplication.

Covers the case where URL-level dedup misses the same article being
indexed under multiple URLs (HuggingFace dataset mirrors, CDN
variants, archive.org vs current URL, etc.).

Approach: after URL-level dedup, compute a SHA-256 fingerprint from
the first 2 KB of normalised body text (title excluded so that
mirrors with slightly different titles still dedup correctly) and
dedup by fingerprint. Best candidate wins (title > no title, then
longer chars).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from deep_dive.aggregator import (
    _content_fingerprint,
    _content_fingerprint_dedup,
    _is_better_candidate,
    Aggregator,
)
from deep_dive.types import (
    AggregatedResult,
    FetchResult,
    FetchStatus,
    TaskResult,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Unit tests for the helpers
# ---------------------------------------------------------------------------

class TestContentFingerprint:
    def test_returns_none_for_empty_inputs(self):
        fr = FetchResult(url="http://x", status=FetchStatus.SUCCESS)
        assert _content_fingerprint(fr) is None

    def test_same_title_same_text_same_fingerprint(self):
        a = FetchResult(
            url="http://a.com/article",
            status=FetchStatus.SUCCESS,
            title="Sample Article",
            txt_path=Path("/tmp/x.txt"),  # doesn't need to exist for empty body
        )
        b = FetchResult(
            url="http://b.com/same-article",
            status=FetchStatus.SUCCESS,
            title="Sample Article",
        )
        # Both have only title, no body. Same title → same fingerprint.
        assert _content_fingerprint(a) == _content_fingerprint(b)

    def test_different_title_different_fingerprint(self):
        a = FetchResult(url="http://a", status=FetchStatus.SUCCESS, title="Article A")
        b = FetchResult(url="http://b", status=FetchStatus.SUCCESS, title="Article B")
        assert _content_fingerprint(a) != _content_fingerprint(b)

    def test_reads_txt_file_body(self, tmp_path):
        p = tmp_path / "body.txt"
        p.write_text("This is the article body with several sentences. " * 50, encoding="utf-8")
        a = FetchResult(
            url="http://a.com/article",
            status=FetchStatus.SUCCESS,
            title="Same Title",
            txt_path=p,
        )
        b = FetchResult(
            url="http://b.com/same-article",
            status=FetchStatus.SUCCESS,
            title="Same Title",
            txt_path=p,
        )
        assert _content_fingerprint(a) == _content_fingerprint(b)

    def test_different_body_different_fingerprint(self, tmp_path):
        a_path = tmp_path / "a.txt"
        a_path.write_text("Article body A", encoding="utf-8")
        b_path = tmp_path / "b.txt"
        b_path.write_text("Article body B — completely different content here", encoding="utf-8")
        a = FetchResult(url="http://a", status=FetchStatus.SUCCESS, title="Same", txt_path=a_path)
        b = FetchResult(url="http://b", status=FetchStatus.SUCCESS, title="Same", txt_path=b_path)
        assert _content_fingerprint(a) != _content_fingerprint(b)


class TestIsBetterCandidate:
    def _fr(self, title="", chars=0):
        return FetchResult(
            url="http://x", status=FetchStatus.SUCCESS, title=title, chars=chars,
        )

    def test_title_wins_over_no_title(self):
        a = self._fr(title="", chars=1000)
        b = self._fr(title="Real Title", chars=100)
        assert _is_better_candidate(b, a) is True
        assert _is_better_candidate(a, b) is False

    def test_tie_break_on_chars(self):
        a = self._fr(title="Same", chars=500)
        b = self._fr(title="Same", chars=2000)
        assert _is_better_candidate(b, a) is True

    def test_chars_breaks_title_tie_when_both_have_title(self):
        # Tie-break rule: when both have title, longer chars wins
        # (more authoritative body). Title *quality* does NOT factor in.
        a = self._fr(title="Short Title", chars=5000)  # longer body
        b = self._fr(title="Real Article Title", chars=500)
        # a has more chars → a is "better" even though title is shorter
        assert _is_better_candidate(a, b) is True
        assert _is_better_candidate(b, a) is False


# ---------------------------------------------------------------------------
# Integration test: dedup on a small URL set
# ---------------------------------------------------------------------------

def _fr_for(url: str, title: str, body: str, tmp_path: Path) -> FetchResult:
    p = tmp_path / (url.replace("/", "_").replace(":", "") + ".txt")
    p.write_text(body, encoding="utf-8")
    return FetchResult(
        url=url,
        status=FetchStatus.SUCCESS,
        title=title,
        chars=len(body),
        txt_path=p,
    )


class TestContentFingerprintDedup:
    def test_dedup_removes_url_with_same_content(self, tmp_path):
        """Two URLs with the same title + body → one is removed."""
        a = _fr_for(
            "http://a.com/article",
            "LLM Leaderboard 2026",
            "Comprehensive guide to LLM benchmarks in 2026. " * 30,
            tmp_path,
        )
        b = _fr_for(
            "http://b.com/mirror",
            "LLM Leaderboard 2026",
            "Comprehensive guide to LLM benchmarks in 2026. " * 30,
            tmp_path,
        )
        url_to_meta = {"http://a.com/article": a, "http://b.com/mirror": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        removed = _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)

        assert removed == 1
        assert len(url_to_meta) == 1
        # Sources/query_indices for the dropped URL must be removed too.
        assert sum(len(v) for v in url_sources.values()) == 1
        assert sum(len(v) for v in url_query_indices.values()) == 1

    def test_dedup_keeps_higher_chars_when_fingerprints_match(self, tmp_path):
        # Same title + same body content → same fingerprint → dedup.
        # Tie-break by chars: keep the one with more chars (the longer
        # body) even when the URL strings differ.
        same_body = "Identical article body " * 30
        a = _fr_for("http://a.com/x", "Title", same_body, tmp_path)
        # Force `chars` to differ even though the body is identical.
        # FetchResult is a frozen dataclass, so use ``dataclasses.replace``
        # to produce a modified copy.
        b = _fr_for("http://b.com/x", "Title", same_body, tmp_path)
        b = dataclasses.replace(b, chars=len(same_body) * 5)
        url_to_meta = {"http://a.com/x": a, "http://b.com/x": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)

        # Dedup should reduce to 1 entry (fingerprints match).
        assert len(url_to_meta) == 1
        kept_url = next(iter(url_to_meta))
        # b has higher chars → b wins (tie-break).
        assert url_to_meta[kept_url].url == "http://b.com/x"
        assert url_to_meta[kept_url].chars == len(same_body) * 5

    def test_dedup_keeps_titled_over_untitled(self, tmp_path):
        # Same body → same fingerprint. Empty title is not included in
        # the fingerprint, so this dedups. The titled version wins.
        same_body = "Long body content shared by both entries " * 30
        a = _fr_for("http://a.com/x", "", same_body, tmp_path)
        b = _fr_for("http://b.com/x", "Real Title", same_body, tmp_path)
        url_to_meta = {"http://a.com/x": a, "http://b.com/x": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)

        assert len(url_to_meta) == 1
        kept_url = next(iter(url_to_meta))
        # b has title, a doesn't → b wins (title preference).
        assert url_to_meta[kept_url].title == "Real Title"
        assert url_to_meta[kept_url].url == "http://b.com/x"

    def test_dedup_idempotent(self, tmp_path):
        a = _fr_for("http://a.com/x", "Same Title", "Same body content " * 30, tmp_path)
        b = _fr_for("http://b.com/x", "Same Title", "Same body content " * 30, tmp_path)
        url_to_meta = {"http://a.com/x": a, "http://b.com/x": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)
        # Second run should be a no-op.
        removed2 = _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)
        assert removed2 == 0
        assert len(url_to_meta) == 1

    def test_dedup_handles_unique_fingerprints(self, tmp_path):
        a = _fr_for("http://a.com/x", "Article A", "Content A " * 30, tmp_path)
        b = _fr_for("http://b.com/x", "Article B", "Content B " * 30, tmp_path)
        url_to_meta = {"http://a.com/x": a, "http://b.com/x": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        removed = _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)
        assert removed == 0
        assert len(url_to_meta) == 2

    def test_dedup_handles_no_body(self, tmp_path):
        """URLs without .txt files or empty bodies should still work
        (fingerprint falls back to title-only)."""
        a = FetchResult(
            url="http://a.com/x", status=FetchStatus.SUCCESS, title="Same Title",
        )  # no txt_path
        b = FetchResult(
            url="http://b.com/x", status=FetchStatus.SUCCESS, title="Same Title",
        )
        url_to_meta = {"http://a.com/x": a, "http://b.com/x": b}
        url_sources = {u: ["src"] for u in url_to_meta}
        url_query_indices = {u: [0] for u in url_to_meta}

        removed = _content_fingerprint_dedup(url_to_meta, url_sources, url_query_indices)
        # Both have title "Same Title", no body → same fingerprint → dedup'd
        assert removed == 1
        assert len(url_to_meta) == 1
