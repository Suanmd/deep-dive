"""Structured ``report.md`` builder.

The report is divided into 4 sections plus an optional "low-quality"
appendix:

1. **任务执行情况** (Task execution) — table of matrix rows × status.
2. **URL 来源汇总** (URL source summary) — table of unique URLs
   (up to 60, sorted by # of source tasks).
3. **全文内容** (Full text content) — body for each URL (success,
   ≥ ``min_chars``, not blacklisted), capped at ``cap`` chars per item.
4. **⚠️ 低质页** (Low quality pages) — items dropped from §3 because
   of low chars or blacklist domain.
5. **元数据** (Metadata) — JSON block with summary stats.

The "global status" (one of ``success`` / ``quota_exceeded`` /
``no_results`` / ``mixed``) prepends a warning block to the report
when it's not ``success``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from deep_dive.constants import LOWQ_DOMAINS, info_density_score, is_low_quality
from deep_dive.logging_setup import safe_print
from deep_dive.types import (
    AggregatedResult,
    FetchResult,
    FetchStatus,
    MatrixRow,
    ResearchPlan,
    TaskResult,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# URL → language guess (used in the §2 source table)
# ---------------------------------------------------------------------------

_CN_HOST_HINTS = (
    ".cn",
    "baidu.com",
    "sogou.com",
    "qq.com",
    "weibo.com",
    "toutiao.com",
    "sohu.com",
    "163.com",
    "bilibili.com",
    "zhihu.com",
    "csdn.net",
    "cnblogs.com",
    "tsinghua.edu.cn",
    "wainao.me",
    "stdaily.com",
    "cctv.com",
    "qidian.com",
    "hao86.com",
    "renrendoc.com",
    "vocus.cc",
    "amazon.co.jp",
)


def detect_lang_from_url(url: str) -> str:
    """Return ``"中文"`` / ``"英文"`` / ``"未知"`` based on URL host."""
    low = url.lower()
    if any(h in low for h in _CN_HOST_HINTS):
        return "中文"
    return "英文"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ascii_slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-")[:60]


def _is_blacklisted_domain(url: str) -> bool:
    low = url.lower()
    return any(d in low for d in LOWQ_DOMAINS)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_report(
    *,
    query: str,
    query_kind: str,
    depth: str,
    lang: str,
    matrix: list[MatrixRow],
    task_results: list[TaskResult],
    aggregated: AggregatedResult,
    output_dir: Path,
    min_chars: int = 500,
    global_status: str = "success",
    cap: int = 12000,
    title_max_chars: int = 50,
    plan: ResearchPlan | None = None,
    matrix_mode: str = "legacy",
    dropped_tasks: list[str] | None = None,
) -> Path:
    """Build the structured ``report.md`` for a run.

    Args:
        query: user's search query.
        query_kind: one of the :class:`QueryKind` values (or ``"general"``).
        depth: ``"quick"`` / ``"normal"`` / ``"full"``.
        lang: ``"zh"`` / ``"en"`` / ``"auto"``.
        matrix: the matrix rows that were dispatched.
        task_results: per-row outcomes.
        aggregated: cross-task de-duplicated results.
        output_dir: where to write ``report.md``.
        min_chars: low-quality threshold (URLs with chars < this go to the appendix).
        global_status: prepends a warning block if not ``"success"``.
        cap: per-URL body cap.
        title_max_chars: title truncation in source table.
        plan: LLM-supplied ResearchPlan. When provided, the
            report renders a 调研策略 section at the top showing
            plan.kind / english_search_terms / variants / target_sites
            / rationale, so the reader can audit the LLM decision.
        matrix_mode: ``"plan"`` or ``"legacy"``. Rendered in
            the header for transparency.
        dropped_tasks: list of plan task labels that were
            truncated due to ``cap``. Rendered as a Cap-truncated
            section so the reader sees why some plan tasks never ran.

    Returns:
        Path to the written ``report.md``.
    """
    report_path = output_dir / "report.md"
    output_dir.mkdir(parents=True, exist_ok=True)

    n_success_tasks = sum(1 for r in task_results if r.status == TaskStatus.SUCCESS)
    url_meta = aggregated.url_meta
    n_success = sum(1 for m in url_meta.values() if m.status == FetchStatus.SUCCESS)
    n_blocked = sum(1 for m in url_meta.values() if m.status == FetchStatus.BLOCKED)
    n_failed = sum(1 for m in url_meta.values() if m.status == FetchStatus.FAILED)

    # Classify URL quality for the rendering decision.
    # P0-2 fix: replaced pure char-count gate with info-density scoring.
    # The previous `if m.chars < min_chars` discarded 21/44 = 48% of URLs
    # in real runs, including data-dense short articles. Now a 250-char
    # news brief with 3 numbers + 1 currency mention clears the bar
    # (density score ~30), while a 5000-char narrative without numbers
    # can still be flagged if density is extreme-low.
    url_meta_rendered: dict[str, FetchResult] = {}
    n_lowq = 0
    n_blacklisted = 0
    for u, m in url_meta.items():
        m = m  # already a FetchResult
        if m.status == FetchStatus.SUCCESS:
            # Read the .txt body for density scoring. Fall back to
            # ``chars`` alone if the txt file is missing (corrupted /
            # deleted mid-run).
            body_text = ""
            if m.txt_path is not None and Path(m.txt_path).exists():
                try:
                    body_text = Path(m.txt_path).read_text("utf-8", errors="ignore")
                except Exception:
                    body_text = ""

            lowq, lowq_reason = is_low_quality(body_text, min_chars=min_chars, url=u)
            if lowq:
                m = FetchResult(
                    url=m.url,
                    status=m.status,
                    title=m.title,
                    chars=m.chars,
                    html_path=m.html_path,
                    txt_path=m.txt_path,
                    error=m.error,
                    source_task=m.source_task,
                    query_index=m.query_index,
                    extra={
                        **m.extra,
                        "render_quality": "low",
                        "lowq_reason": lowq_reason,
                        "info_density": info_density_score(body_text),
                    },
                )
                n_lowq += 1
            elif _is_blacklisted_domain(u):
                m = FetchResult(
                    url=m.url,
                    status=m.status,
                    title=m.title,
                    chars=m.chars,
                    html_path=m.html_path,
                    txt_path=m.txt_path,
                    error=m.error,
                    source_task=m.source_task,
                    query_index=m.query_index,
                    extra={**m.extra, "render_quality": "low", "lowq_reason": "blacklisted_domain"},
                )
                n_blacklisted += 1
            else:
                m = FetchResult(
                    url=m.url,
                    status=m.status,
                    title=m.title,
                    chars=m.chars,
                    html_path=m.html_path,
                    txt_path=m.txt_path,
                    error=m.error,
                    source_task=m.source_task,
                    query_index=m.query_index,
                    extra={
                        **m.extra,
                        "render_quality": "ok",
                        "info_density": info_density_score(body_text),
                    },
                )
        url_meta_rendered[u] = m

    url_sources = aggregated.url_sources
    sorted_urls = sorted(url_meta_rendered.keys(), key=lambda u: -len(url_sources.get(u, ())))

    with open(report_path, "w", encoding="utf-8-sig", buffering=1024 * 16) as f:

        def w(batch: list[str]) -> None:
            """Flush ``batch`` to the report file (skips empty batches)."""
            if batch:
                f.write("\n".join(batch) + "\n")
                f.flush()

        # ----- Header + global warnings ----------------------------------
        lines: list[str] = [f"# Deep Dive Report: {query}", ""]
        if global_status == "quota_exceeded":
            lines += [
                "> ⚠️ **[QUOTA 警告]** Multiple tasks hit MMX quota exhaustion (`exceeds your plan`).",
                "> Suggestions: 1) wait 4h and retry; 2) reduce `--depth=quick`; 3) add `--no-tavily`.",
                "",
            ]
        elif global_status == "no_results":
            lines += [
                "> ⚠️ **[EMPTY 警告]** All tasks returned 0 URLs.",
                "> Possible causes: query too broad, MMX miss, network issue. Check `raw/metadata.json`.",
                "",
            ]
        elif global_status == "mixed":
            lines += [
                "> ⚠️ **[MIXED 警告]** Some tasks succeeded, others quota/empty. Use `summary.json` to triage.",
                "",
            ]

        lines += [
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
            f"**查询类型**：{query_kind}  ",
            f"**搜索深度**：{depth}  ",
            f"**语言**：{lang}  ",
            f"**任务总数**：{len(matrix)}（成功 {n_success_tasks}）  ",
            f"**URL 总数**：{len(aggregated.unique_urls)}  ",
            f"**结果**：成功 {n_success} / 拦截 {n_blocked} / 失败 {n_failed} / 低质（<{min_chars}字）{n_lowq} / 黑名单域名 {n_blacklisted}  ",
            f"**运行模式**：{matrix_mode}  ",
            "",
        ]

        # ----- 调研策略 section (LLM-supplied plan) -----
        if plan is not None:
            lines += [
                "## 调研策略 (LLM Plan)",
                "",
                "本 run 由 LLM 生成的 ResearchPlan 驱动。决策依据如下：",
                "",
                f"- **query**: `{plan.query}`",
                f"- **kind**: `{plan.kind}` (LLM 分类)",
                f"- **language_priority**: `{plan.language_priority}`",
                f"- **relevance_threshold**: `{plan.relevance_threshold}`",
                "",
            ]
            if plan.english_search_terms:
                lines += ["**English search terms**:", ""]
                for i, term in enumerate(plan.english_search_terms, 1):
                    lines.append(f"{i}. `{term}`")
                lines.append("")
            if plan.variants:
                lines += ["**Chinese variants**:", ""]
                for k, v in plan.variants.items():
                    lines.append(f"- **{k}**: `{v}`")
                lines.append("")
            if plan.target_sites:
                lines += ["**Target sites (按 LLM 优先级排序)**:", ""]
                for s in plan.target_sites:
                    lines.append(f"- `{s}`")
                lines.append("")
            if plan.rationale:
                lines += [
                    "**LLM rationale**:",
                    "",
                    f"> {plan.rationale}",
                    "",
                ]

        # ----- Cap-truncated tasks warning ----------------
        if dropped_tasks:
            lines += [
                "## ⚠️ Cap 截断的任务 (未被执行)",
                "",
                f"由于 cap={len(matrix)} 限制，以下 plan 任务未被执行（按优先级裁切）：",
                "",
            ]
            for label in dropped_tasks:
                lines.append(f"- `{label}`")
            lines += [
                "",
                "> 提示：使用 `--depth full` 可将 cap 提升到 14，装下更多任务。",
                "",
            ]

        # ----- §1 Task execution table --------------------------------
        lines += ["## 1. 任务执行情况", "", "| # | 任务 | 状态 |", "|---|------|------|"]
        for i, (row, res) in enumerate(zip(matrix, task_results, strict=True), 1):
            status_label = "OK" if res.status == TaskStatus.SUCCESS else res.status.value
            lines.append(f"| {i} | {row.note} | {status_label} |")
        lines.append("")

        # ----- §2 URL source summary ----------------------------------
        lines += [
            "## 2. URL 来源汇总",
            "",
            f"**共 {len(sorted_urls)} 个独立 URL** (sorted by # of source tasks)",
            "",
            "| # | 标题 | URL | 字数 | 来源 |",
            "|---|------|-----|------|------|",
        ]
        for i, u in enumerate(sorted_urls[:60], 1):
            m = url_meta_rendered[u]
            title = (m.title or "无标题").replace("|", " ")[:title_max_chars]
            src = ", ".join(url_sources.get(u, ()))[:40]
            lines.append(f"| {i} | {title} | {u[:50]}... | {m.chars} | {src} |")
        lines.append("")
        w(lines)

        # ----- §3 Full-text content ------------------------------------
        lines = ["## 3. 全文内容", "", "> 写作素材，按字数倒序", ""]
        w(lines)
        lines = []

        # Sort items by char count desc, then iterate
        items = sorted(url_meta_rendered.items(), key=lambda kv: -kv[1].chars)
        written = 0
        skipped_lowq = 0

        # Pre-build txt-path lookup from aggregated file existence
        txt_paths_by_name: dict[str, Path] = {}
        for tr in task_results:
            if tr.output_dir is None:
                continue
            if not tr.output_dir.exists():
                continue
            for tf in tr.output_dir.glob("*.txt"):
                txt_paths_by_name[tf.name] = tf

        for idx, (u, m) in enumerate(items, 1):
            if m.extra.get("render_quality") == "low":
                skipped_lowq += 1
                continue
            txt_file = m.txt_path
            # Try to find the txt file in our lookup
            if txt_file is not None:
                txt_file_path = Path(txt_file)
                # Absolute or relative
                if not txt_file_path.exists():
                    candidate_name = txt_file_path.name
                    resolved = txt_paths_by_name.get(candidate_name)
                    txt_file_path = resolved if resolved is not None else None  # type: ignore[assignment]
            else:
                # Legacy fallback: metadata may have stored just the basename
                candidate_name = u
                txt_file_path = None

            if txt_file_path is None or not txt_file_path.exists():
                continue

            try:
                body = txt_file_path.read_text("utf-8", errors="ignore")[:cap]
            except Exception:
                continue

            lines += [
                f"### {idx}. {m.title or '无标题'}",
                f"**URL**: {u}",
                f"**来源**: {m.source_task}",
                "```",
                body,
                "```",
                "",
                "---",
                "",
            ]
            written += 1
            if written % 5 == 0:
                w(lines)
                lines = []

        # ----- §4 Low-quality appendix --------------------------------
        if n_lowq > 0 or n_blacklisted > 0:
            lowq_items = [
                (u, m) for u, m in url_meta_rendered.items() if m.extra.get("render_quality") == "low"
            ]
            lowq_items.sort(key=lambda kv: kv[1].chars)
            lines += [f"### ⚠️ 低质页（{len(lowq_items)} 个，已从正文剔除）", ""]
            for u, m in lowq_items[:30]:
                title = (m.title or "无标题").replace("|", " ")[:60]
                reason = m.extra.get("lowq_reason", "?")
                lines.append(f"- {m.chars} bytes | [{reason}] | {title} | {u[:60]}")
            lines += ["", "---", ""]
            w(lines)
            lines = []

        # ----- §5 Metadata JSON ---------------------------------------
        summary_block = {
            "query": query,
            "type": query_kind,
            "depth": depth,
            "tasks": len(matrix),
            "success_tasks": n_success_tasks,
            "unique_urls": len(sorted_urls),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "global_status": global_status,
        }
        lines += [
            "## 4. 元数据",
            "",
            "```json",
            json.dumps(summary_block, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        w(lines)

    safe_print(f"{report_path.name} written ({written} items rendered, {skipped_lowq} low-quality)")
    return report_path


__all__ = ["build_report", "detect_lang_from_url"]
