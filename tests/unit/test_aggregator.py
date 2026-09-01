"""Tests for cross-task URL aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from deep_dive.aggregator import Aggregator
from deep_dive.types import TaskResult, TaskStatus


def _write_metadata(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


class TestAggregatorBasic:
    def test_no_tasks_returns_empty(self, tmp_path):
        agg = Aggregator()
        result = agg.aggregate([], tmp_path / "raw")
        assert result.total_urls == 0
        assert result.unique_urls == ()
        assert result.url_meta == {}
        assert result.global_status == "success"

    def test_aggregates_single_task(self, tmp_raw_dir):
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        _write_metadata(
            task_dir / "metadata.json",
            [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "status": "success",
                    "chars": 100,
                    "txt_file": "a.txt",
                    "source_task": "task_00",
                },
            ],
        )
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.SUCCESS, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 1
        assert "https://example.com/a" in result.unique_urls
        assert result.url_meta["https://example.com/a"].title == "A"

    def test_failed_tasks_skipped(self, tmp_raw_dir):
        # Even if metadata.json exists for a failed task, it's skipped
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        _write_metadata(
            task_dir / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success"},
            ],
        )
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.FAILED, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 0

    def test_quota_exceeded_tasks_counted(self, tmp_raw_dir):
        """Quota-exceeded tasks should contribute their harvested URLs to the
        aggregator rather than being silently dropped."""
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        _write_metadata(
            task_dir / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success"},
            ],
        )
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.QUOTA_EXCEEDED, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 1


class TestCrossTaskDedup:
    def test_same_url_in_two_tasks(self, tmp_raw_dir):
        t1 = tmp_raw_dir / "task_00"
        t2 = tmp_raw_dir / "task_01"
        t1.mkdir()
        t2.mkdir()
        _write_metadata(
            t1 / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success", "title": "T1"},
            ],
        )
        _write_metadata(
            t2 / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success", "title": "T2"},
            ],
        )
        results = [
            TaskResult(note="t1", query="test", status=TaskStatus.SUCCESS, output_dir=t1),
            TaskResult(note="t2", query="test", status=TaskStatus.SUCCESS, output_dir=t2),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        # One unique URL
        assert out.total_urls == 1
        # Source tracking
        sources = out.url_sources["https://example.com/a"]
        assert "t1" in sources
        assert "t2" in sources

    def test_prefers_metadata_with_title(self, tmp_raw_dir):
        t1 = tmp_raw_dir / "task_00"
        t2 = tmp_raw_dir / "task_01"
        t1.mkdir()
        t2.mkdir()
        _write_metadata(
            t1 / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success", "title": ""},
            ],
        )
        _write_metadata(
            t2 / "metadata.json",
            [
                {"url": "https://example.com/a", "status": "success", "title": "Real Title"},
            ],
        )
        results = [
            TaskResult(note="t1", query="test", status=TaskStatus.SUCCESS, output_dir=t1),
            TaskResult(note="t2", query="test", status=TaskStatus.SUCCESS, output_dir=t2),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        assert out.url_meta["https://example.com/a"].title == "Real Title"


class TestMetadataParsing:
    def test_invalid_json_skipped(self, tmp_raw_dir):
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        (task_dir / "metadata.json").write_text("not json", encoding="utf-8")
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.SUCCESS, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 0

    def test_missing_metadata_file(self, tmp_raw_dir):
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        # No metadata.json
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.SUCCESS, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 0

    def test_non_list_metadata(self, tmp_raw_dir):
        task_dir = tmp_raw_dir / "task_00"
        task_dir.mkdir()
        _write_metadata(task_dir / "metadata.json", {"not": "a list"})
        tr = TaskResult(note="task_00", query="test", status=TaskStatus.SUCCESS, output_dir=task_dir)
        agg = Aggregator()
        result = agg.aggregate([tr], tmp_raw_dir)
        assert result.total_urls == 0

    def test_extra_field_serialized_to_summary(self):
        """Regression: summary.json must include the extra field
        (engine, fallback_used, n_attempted, ...) so downstream tooling
        can audit the fallback chain without re-reading stderr."""
        from deep_dive.orchestrator import _task_to_json

        tr = TaskResult(
            note="test",
            query="q",
            status=TaskStatus.SUCCESS,
            url_count=3,
            duration_seconds=1.5,
            extra={"engine": "mmx", "fallback_used": True, "fallback_status": "ok", "n_attempted": 5},
        )
        data = _task_to_json(tr)
        assert "extra" in data
        assert data["extra"]["engine"] == "mmx"
        assert data["extra"]["fallback_used"] is True
        assert data["extra"]["fallback_status"] == "ok"
        assert data["extra"]["n_attempted"] == 5

    def test_empty_extra_omitted_from_summary(self):
        """When a task has no extras (e.g. failed before engine dispatch),
        the summary should NOT include an empty 'extra' field."""
        from deep_dive.orchestrator import _task_to_json

        tr = TaskResult(note="test", query="q", status=TaskStatus.FAILED)
        data = _task_to_json(tr)
        assert "extra" not in data


class TestGlobalStatus:
    def test_all_success(self, tmp_raw_dir):
        results = [
            TaskResult(note="t", query="q", status=TaskStatus.SUCCESS),
            TaskResult(note="t", query="q", status=TaskStatus.SUCCESS),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        assert out.global_status == "success"

    def test_mostly_quota(self, tmp_raw_dir):
        results = [
            TaskResult(note="t", query="q", status=TaskStatus.QUOTA_EXCEEDED),
            TaskResult(note="t", query="q", status=TaskStatus.QUOTA_EXCEEDED),
            TaskResult(note="t", query="q", status=TaskStatus.SUCCESS),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        assert out.global_status == "quota_exceeded"

    def test_all_no_results(self, tmp_raw_dir):
        results = [
            TaskResult(note="t", query="q", status=TaskStatus.NO_RESULTS),
            TaskResult(note="t", query="q", status=TaskStatus.NO_RESULTS),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        assert out.global_status == "no_results"

    def test_mixed(self, tmp_raw_dir):
        results = [
            TaskResult(note="t", query="q", status=TaskStatus.SUCCESS),
            TaskResult(note="t", query="q", status=TaskStatus.FAILED),
        ]
        agg = Aggregator()
        out = agg.aggregate(results, tmp_raw_dir)
        assert out.global_status == "mixed"
