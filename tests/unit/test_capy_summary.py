"""Tests for the Capy summary section builder."""

from __future__ import annotations

import re

from deep_dive.reporting.capy_summary import (
    _extract_content_views,
    append_capy_section,
)
from deep_dive.types import FetchResult, FetchStatus, TaskResult, TaskStatus


def _success_result(url: str, title: str = "", chars: int = 1000) -> FetchResult:
    return FetchResult(
        url=url, status=FetchStatus.SUCCESS,
        title=title, chars=chars,
        source_task="task_00",
    )


class TestExtractContentViews:
    def test_empty_report(self):
        views = _extract_content_views("", "长鑫")
        assert views == {}

    def test_parses_article_sections(self):
        report = """### 1. 长鑫存储 DRAM 产能
```
长鑫存储 DRAM 产能预计激增
长鑫科技市场份额将提升
```
"""
        views = _extract_content_views(report, "长鑫 DRAM")
        assert views["article_count"] == 1

    def test_key_phrases_extracted(self):
        report = """### 1. 长鑫存储
```
长鑫存储 2026 年 DRAM 产能预计激增 30%。
机构预测长鑫存储市场份额将提升至 8%。
```
"""
        views = _extract_content_views(report, "长鑫存储 DRAM")
        phrases = [p for p, c in views["key_phrases"]]
        assert "长鑫存储" in phrases

    def test_themed_clustering(self):
        report = """### 1. Price Target Article
```
机构预测 目标价 350 美元。预计 2026 年突破新高。
```
"""
        views = _extract_content_views(report, "stock price target")
        # The "predictive" cluster should have entries
        assert len(views["themed_clusters"]["predictive"]) > 0


class TestAppendCapySection:
    def test_append_when_data_present(self, tmp_path, sample_report_md):
        results = [
            TaskResult(note="task_00", query="长鑫存储", status=TaskStatus.SUCCESS),
        ]
        aggregated = [
            _success_result("https://example.com/a", title="长鑫存储 DRAM 分析", chars=1500),
            _success_result("https://example.com/b", title="市场份额报告", chars=800),
        ]
        ok = append_capy_section(
            report_path=sample_report_md,
            query="长鑫存储",
            task_results=results,
            aggregated_meta=aggregated,
        )
        assert ok is True
        content = sample_report_md.read_text(encoding="utf-8")
        assert "卡皮观点" in content
        # Should mention total
        assert "总 URL" in content

    def test_empty_data_writes_status_block(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("# Report\n\nContent here.\n", encoding="utf-8")
        results = [
            TaskResult(note="task_00", query="test", status=TaskStatus.NO_RESULTS),
        ]
        ok = append_capy_section(
            report_path=report,
            query="test",
            task_results=results,
            aggregated_meta=[],  # empty
        )
        assert ok is True
        content = report.read_text(encoding="utf-8")
        assert "[EMPTY]" in content or "[QUOTA]" in content
        # Should NOT contain the full Capy section
        assert "总 URL" not in content

    def test_quota_data_writes_quota_block(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("# Report\n\nContent.\n", encoding="utf-8")
        results = [
            TaskResult(note="t", query="test", status=TaskStatus.QUOTA_EXCEEDED),
        ]
        ok = append_capy_section(
            report_path=report,
            query="test",
            task_results=results,
            aggregated_meta=[],
        )
        content = report.read_text(encoding="utf-8")
        assert "[QUOTA]" in content

    def test_idempotent_removes_old_section(self, tmp_path):
        report = tmp_path / "report.md"
        # Start with a report that already has a Capy section
        initial = "# Report\n\n---\n\n## 🎀 卡皮观点（旧内容）\n\nold data\n"
        report.write_text(initial, encoding="utf-8")

        results = [TaskResult(note="t", query="test", status=TaskStatus.SUCCESS)]
        aggregated = [_success_result("https://example.com/a", chars=1500)]
        append_capy_section(
            report_path=report, query="test",
            task_results=results, aggregated_meta=aggregated,
        )
        content = report.read_text(encoding="utf-8")
        # Old section content should be gone
        assert "旧内容" not in content
        # New section should be present
        assert "卡皮观点（自动生成" in content

    def test_nonexistent_report(self, tmp_path):
        ok = append_capy_section(
            report_path=tmp_path / "missing.md",
            query="test",
            task_results=[],
            aggregated_meta=[],
        )
        assert ok is False


class TestAppendCapySection_LegacyParity:
    def test_does_not_crash_on_zero_chars(self, tmp_path):
        """Regression: legacy code had a \"千位分隔符\" bug — verify our impl doesn't crash."""
        report = tmp_path / "report.md"
        report.write_text("# Report\n", encoding="utf-8")
        aggregated = [_success_result("https://example.com/a", chars=0)]
        results = [TaskResult(note="t", query="test", status=TaskStatus.SUCCESS)]
        ok = append_capy_section(
            report_path=report, query="test",
            task_results=results, aggregated_meta=aggregated,
        )
        # Should succeed despite chars=0
        assert ok is True


class TestFailedTaskListing:
    """Explicitly surface failed tasks so users can decide whether
    to retry, switch engine, or rephrase the query."""

    def test_failure_section_appears_when_tasks_failed(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("# Report\n\nContent here.\n", encoding="utf-8")
        results = [
            TaskResult(
                note="中文原始", query="算电协同",
                status=TaskStatus.SUCCESS,
            ),
            TaskResult(
                note="中文评论/反对视角", query="算电协同",
                status=TaskStatus.QUOTA_EXCEEDED,
                error="mmx quota exhausted",
                extra={"engine": "mmx", "degraded_to": "tavily"},
            ),
            TaskResult(
                note="站点定向:stackoverflow.com", query="算电协同",
                status=TaskStatus.NO_RESULTS,
                extra={"engine": "mmx", "site_filtered_out": True,
                       "target_site": "stackoverflow.com"},
            ),
        ]
        aggregated = [_success_result("https://example.com/a", chars=1500)]
        ok = append_capy_section(
            report_path=report, query="算电协同",
            task_results=results, aggregated_meta=aggregated,
        )
        assert ok is True
        content = report.read_text(encoding="utf-8")
        # Failure-listing heading must appear.
        assert "失败任务清单" in content
        # Each failed task should appear by name.
        assert "中文评论/反对视角" in content
        assert "站点定向:stackoverflow.com" in content
        # Status strings appear in the table.
        assert "quota_exceeded" in content
        assert "no_results" in content

    def test_failure_section_absent_when_all_succeeded(self, tmp_path):
        report = tmp_path / "report.md"
        report.write_text("# Report\n\nContent.\n", encoding="utf-8")
        results = [
            TaskResult(note="t1", query="x", status=TaskStatus.SUCCESS),
            TaskResult(note="t2", query="x", status=TaskStatus.SUCCESS),
        ]
        aggregated = [_success_result("https://example.com/a", chars=1500)]
        append_capy_section(
            report_path=report, query="x",
            task_results=results, aggregated_meta=aggregated,
        )
        content = report.read_text(encoding="utf-8")
        # No failures → no failure-listing heading.
        assert "失败任务清单" not in content
