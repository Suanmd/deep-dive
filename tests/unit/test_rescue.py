"""Tests for the auto-rescue raw file combiner."""

from __future__ import annotations

from pathlib import Path

from deep_dive.rescue import auto_rescue_raw
from deep_dive.types import AggregatedResult


def _write_txt(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _empty_aggregated() -> AggregatedResult:
    return AggregatedResult(
        total_urls=0,
        unique_urls=(),
        url_meta={},
        all_meta=(),
        url_sources={},
        url_query_indices={},
        global_status="no_results",
    )


class TestAutoRescue:
    def test_skip_when_raw_all_exists(self, tmp_path):
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        raw = topic / "raw"
        raw.mkdir()
        (topic / "topic__run1_raw_all.txt").write_text("X" * 2000, encoding="utf-8")
        _write_txt(raw / "task_00" / "a.txt", "Some text " * 50)

        result = auto_rescue_raw(
            topic_dir=topic, raw_dir=raw, aggregated=_empty_aggregated()
        )
        # Returned the existing path without rewriting
        assert result[2] is not None
        assert "raw_all.txt" in result[2]

    def test_no_raw_dir(self, tmp_path):
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        # raw/ does not exist
        result = auto_rescue_raw(
            topic_dir=topic, raw_dir=topic / "raw", aggregated=_empty_aggregated()
        )
        assert result[2] is None

    def test_no_txt_files(self, tmp_path):
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        raw = topic / "raw"
        raw.mkdir()
        # Empty raw
        result = auto_rescue_raw(
            topic_dir=topic, raw_dir=raw, aggregated=_empty_aggregated()
        )
        assert result[2] is None

    def test_builds_raw_all_from_txt(self, tmp_path):
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        raw = topic / "raw"
        raw.mkdir()
        t = raw / "task_00"
        t.mkdir()
        # Each paragraph must be >= 80 chars to pass the rescue threshold
        long_para = (
            "This is a long paragraph that should be retained by the rescue "
            "logic because it contains meaningful content beyond navigation. "
        )
        long_content = long_para + "\n\n" + long_para + "\n\n" + long_para
        _write_txt(t / "article.txt", long_content)

        n_files, n_chars, out_path = auto_rescue_raw(
            topic_dir=topic, raw_dir=raw, aggregated=_empty_aggregated()
        )
        assert n_files == 1
        assert n_chars > 0
        assert out_path is not None
        written = Path(out_path).read_text(encoding="utf-8")
        assert len(written) > 0
        assert "long paragraph" in written

    def test_paragraph_sha1_dedup(self, tmp_path):
        # Two files with the same paragraph → paragraph appears once
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        raw = topic / "raw"
        raw.mkdir()
        shared = (
            "This is a paragraph that should appear only once in the output. "
            "It needs to be at least eighty characters long to pass the filter, "
            "so we pad it with more content until it comfortably exceeds the "
            "minimum paragraph length threshold used by the rescue logic."
        )
        _write_txt(raw / "a.txt", shared)
        _write_txt(raw / "b.txt", shared)

        n_files, n_chars, out_path = auto_rescue_raw(
            topic_dir=topic, raw_dir=raw, aggregated=_empty_aggregated()
        )
        written = Path(out_path).read_text(encoding="utf-8")
        # Count occurrences of the shared string
        assert written.count("This is a paragraph") == 1

    def test_short_paragraphs_skipped(self, tmp_path):
        # Paragraphs shorter than 80 chars are navigation noise
        topic = tmp_path / "topic__run1"
        topic.mkdir()
        raw = topic / "raw"
        raw.mkdir()
        short_para = "short"  # 5 chars, far below the 80-char threshold
        long_para = "x" * 200  # well above threshold
        _write_txt(raw / "a.txt", short_para)
        _write_txt(raw / "b.txt", long_para)

        n_files, n_chars, out_path = auto_rescue_raw(
            topic_dir=topic, raw_dir=raw, aggregated=_empty_aggregated()
        )
        # The short paragraph should be filtered out, the long one kept
        written = Path(out_path).read_text(encoding="utf-8")
        assert "short" not in written
        assert "x" * 50 in written
