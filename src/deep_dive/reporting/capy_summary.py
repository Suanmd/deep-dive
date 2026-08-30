"""Capy summary section.

The Capy summary is appended to the bottom of ``report.md`` after the
main 4-section report. It contains:

* Metadata snapshot (URL count, success/blocked/failed, language split).
* Per-task status distribution.
* Top-5 URLs by character count.
* **Content-based** thematic clustering (key_phrases / themed_clusters
  / key_quotes) extracted from the actual ``report.md`` body.
* Three content views (主题归纳 / 多空博弈 / 数据质量).
* One-line summary.

If the run produced zero URLs (or all blocked), the function writes a
short ``[EMPTY]`` / ``[QUOTA]`` status block instead — **never**
fabricates "3 viewpoints" out of nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from deep_dive.constants import (
    BEAR_KEYWORDS,
    BULL_KEYWORDS,
    CN_STOPWORDS,
    EN_STOPWORDS,
    PREDICTIVE_KEYWORDS,
    TAG_CFY,
    TAG_WARN,
)
from deep_dive.logging_setup import safe_print
from deep_dive.types import FetchResult, FetchStatus, TaskResult, TaskStatus

from .builder import detect_lang_from_url


# ---------------------------------------------------------------------------
# Content view extraction (logic)
# ---------------------------------------------------------------------------

_ARTICLE_RE = re.compile(r"^###\s+(?:\d+\.\s+)?(.+)$", re.MULTILINE)
_CN_PHRASE_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")
_EN_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_SENTENCE_RE = re.compile(r"[^。！？.!?\n]{10,200}[。！？.!?]?")


def _extract_content_views(report_text: str, query: str, max_chars_per_article: int = 800) -> dict[str, Any]:
    """Extract thematic views from a fully-built ``report.md``.

    Args:
        report_text: full report body (UTF-8).
        query: the user's query (used to filter relevant phrases).
        max_chars_per_article: cap per article body when scanning.

    Returns:
        Dict with keys:
        - ``article_count``: number of ``### N. title`` sections parsed
        - ``key_phrases``: top 20 (phrase, count) pairs
        - ``themed_clusters``: dict of category → list[str] sentences
        - ``key_quotes``: top 5 numeric / quoted sentences
        - ``high_chars_articles``: top 5 articles by char count
    """
    if not report_text or not query:
        return {}

    matches = list(_ARTICLE_RE.finditer(report_text))
    if not matches:
        return {}

    articles: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        body = report_text[start:end].strip()
        articles.append({
            "title": title,
            "body": body[:max_chars_per_article],
            "full_chars": len(body),
        })

    # --- Build keyword set from query (for filtering) ---------------
    query_kws: set[str] = set()
    for c in query:
        if "\u4e00" <= c <= "\u9fff":
            query_kws.add(c)
    for w in _EN_WORD_RE.findall(query.lower()):
        query_kws.add(w.lower())

    # --- Phrase counting -------------------------------------------
    counter: Counter[str] = Counter()
    for art in articles:
        text = art["body"]
        for phrase in _CN_PHRASE_RE.findall(text):
            if phrase in CN_STOPWORDS:
                continue
            if any(s in phrase for s in CN_STOPWORDS if len(s) >= 2):
                continue
            if not any(kw in phrase for kw in query_kws):
                continue
            counter[phrase] += 1
        for word in _EN_WORD_RE.findall(text):
            wl = word.lower()
            if wl in EN_STOPWORDS or len(wl) < 3:
                continue
            if not any(kw in wl for kw in query_kws):
                continue
            counter[wl] += 1
    top_phrases = counter.most_common(20)

    # --- Themed clustering ----------------------------------------
    themed: dict[str, list[str]] = {
        "predictive": [],
        "argument_bull": [],
        "argument_bear": [],
        "fact": [],
    }
    for art in articles:
        for sent in _SENTENCE_RE.findall(art["body"]):
            sent = sent.strip()
            if len(sent) < 10:
                continue
            if any(kw in sent for kw in PREDICTIVE_KEYWORDS):
                themed["predictive"].append(sent)
            elif any(kw in sent for kw in BEAR_KEYWORDS):
                themed["argument_bear"].append(sent)
            elif any(kw in sent for kw in BULL_KEYWORDS):
                themed["argument_bull"].append(sent)
            elif re.search(r"\d{2,}", sent):
                themed["fact"].append(sent)

    # Dedupe and cap each cluster
    for k in themed:
        seen: set[str] = set()
        unique: list[str] = []
        for s in themed[k]:
            if s not in seen:
                seen.add(s)
                unique.append(s)
        themed[k] = unique[:3]

    # --- Key quotes (sentences with numbers / units) ---------------
    quotes: list[str] = []
    seen_q: set[str] = set()
    for art in articles:
        for sent in _SENTENCE_RE.findall(art["body"]):
            sent = sent.strip()
            if re.search(r"^https?://|/article/|/doc-|cn/[a-z]+/|com/[a-z]+/", sent):
                continue
            if len(sent) < 30:
                continue
            if re.search(r"\d+[%美元/吨/盎司/倍]", sent) or re.search(r"\d{4,}", sent):
                if sent not in seen_q:
                    seen_q.add(sent)
                    quotes.append(sent[:200])
                    if len(quotes) >= 5:
                        break
        if len(quotes) >= 5:
            break

    # Clean fact cluster from URL residue
    seen_fact: set[str] = set()
    fact_clean: list[str] = []
    for s in themed["fact"]:
        if re.search(r"^https?://|/article/|/doc-|cn/[a-z]+/|com/[a-z]+/", s):
            continue
        if s in seen_fact:
            continue
        seen_fact.add(s)
        fact_clean.append(s)
    themed["fact"] = fact_clean[:3]

    high_chars = sorted(articles, key=lambda a: a["full_chars"], reverse=True)[:5]
    high_chars_summary = [(a["title"][:50], a["full_chars"]) for a in high_chars]

    return {
        "article_count": len(articles),
        "key_phrases": top_phrases,
        "themed_clusters": themed,
        "key_quotes": quotes,
        "high_chars_articles": high_chars_summary,
    }


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------

def _build_section(query: str, task_results: list[TaskResult], aggregated_meta: list[FetchResult], report_text: str = "") -> str:
    """Build the markdown text of the Capy section."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = len(aggregated_meta) if aggregated_meta else 0
    n_success = sum(1 for m in (aggregated_meta or []) if m.status == FetchStatus.SUCCESS)
    n_blocked = sum(1 for m in (aggregated_meta or []) if m.status == FetchStatus.BLOCKED)
    n_failed = sum(1 for m in (aggregated_meta or []) if m.status == FetchStatus.FAILED)

    lang_counter: dict[str, int] = {"中文": 0, "英文": 0}
    if aggregated_meta:
        for m in aggregated_meta:
            lang_counter[detect_lang_from_url(m.url)] += 1

    top5 = sorted(
        [m for m in (aggregated_meta or []) if m.status == FetchStatus.SUCCESS],
        key=lambda m: m.chars, reverse=True,
    )[:5]
    top5_lines: list[str] = []
    for i, m in enumerate(top5, 1):
        lang = detect_lang_from_url(m.url)
        top5_lines.append(f"  {i}. [{lang}] {m.title[:60]}")
        top5_lines.append(f"     {m.url[:80]}")
        top5_lines.append(f"     字数: {m.chars}")
    top5_block = "\n".join(top5_lines) if top5_lines else "  (无)"

    task_lines = []
    failed_lines: list[str] = []
    for tr in task_results:
        task_lines.append(f"  - {tr.note}: {tr.status.value}")
        if tr.status != TaskStatus.SUCCESS:
            # Surface failures explicitly so the user can decide
            # whether to retry, switch engine, or rephrase query.
            error = (tr.error or "-").replace("|", "/").replace("\n", " ")[:80]
            engine = tr.extra.get("engine", "?") if tr.extra else "?"
            degraded = tr.extra.get("degraded_to") or "-" if tr.extra else "-"
            failed_lines.append(
                f"| {tr.note} | {tr.status.value} | {error} | {engine} | {degraded} |"
            )
    task_block = "\n".join(task_lines) if task_lines else "  - (无 task 元数据)"

    # Build a dedicated failure-listing block. Hidden when
    # all tasks succeeded; explicit table when any task failed.
    failed_block = ""
    if failed_lines:
        failed_block = (
            "\n### ⚠️ 失败任务清单\n\n"
            "| 任务 | 状态 | 错误 | 引擎 | 降级 |\n"
            "|------|------|------|------|------|\n"
            + "\n".join(failed_lines)
            + "\n"
        )

    cv = _extract_content_views(report_text or "", query)

    if cv.get("themed_clusters"):
        tc = cv["themed_clusters"]
        themed_block_lines: list[str] = []
        for theme_name, label in [
            ("predictive", "预测/机构观点"),
            ("argument_bull", "看涨论据"),
            ("argument_bear", "看跌论据"),
            ("fact", "事实/数据"),
        ]:
            items = tc.get(theme_name, [])
            if items:
                themed_block_lines.append(f"\n**{label}**：")
                for it in items:
                    themed_block_lines.append(f"- {it[:150]}")
        themed_block = "\n".join(themed_block_lines) or "\n(未读出内容主题分类)\n"
    else:
        themed_block = "\n(未读出内容主题分类)\n"

    if cv.get("key_phrases"):
        phrase_block = "\n".join(f"  - `{p}` x {c}" for p, c in cv["key_phrases"][:10])
    else:
        phrase_block = "  (无)"

    if cv.get("key_quotes"):
        quote_block = "\n".join(f"\n  > {q[:180]}\n" for q in cv["key_quotes"][:3]) or "\n  (无)\n"
    else:
        quote_block = "\n  (无)\n"

    n_pred = len(cv.get("themed_clusters", {}).get("predictive", []))
    n_bull = len(cv.get("themed_clusters", {}).get("argument_bull", []))
    n_bear = len(cv.get("themed_clusters", {}).get("argument_bear", []))
    n_fact = len(cv.get("themed_clusters", {}).get("fact", []))

    views = [
        f"**观点 1（内容主题）**：从 report.md {cv.get('article_count', 0)} 个文章 section 抽取高频词，中文短语 + 英文词 Top 10：`{', '.join(f'{p}({c})' for p, c in cv.get('key_phrases', [])[:5])}` —— 这代表了本次抓取的主题焦点。",
        f"**观点 2（多空博弈）**：预测/机构观点 {n_pred} 条 + 看涨论据 {n_bull} 条 + 看跌论据 {n_bear} 条 + 事实/数据 {n_fact} 条。"
        + ("看涨论据略多于看跌" if n_bull > n_bear else "看跌论据略多于看涨" if n_bear > n_bull else "多空平衡"),
        f"**观点 3（数据质量）**：抓取 {total} 个独立 URL（成功 {n_success}），"
        f"中文 {lang_counter.get('中文', 0)} 个 / 英文 {lang_counter.get('英文', 0)} 个。"
        f"报告头部 Top 5 长文 = `{' / '.join(m.title[:30] for m in top5[:3])}` —— 信噪比最高的素材。",
    ]
    capy_block = "\n\n".join(views)

    top_phrase = cv.get("key_phrases", [("", 0)])[0][0] if cv.get("key_phrases") else "N/A"
    one_liner = (
        f"**一句话总结**：本次深度研究围绕「{query}」抓取 {total} 个独立 URL，"
        f"覆盖 {len(task_results)} 个搜索任务维度，成功 {n_success}（{n_success * 100 // max(total, 1)}%）。"
        f"主题焦点 = `{top_phrase}`。深度阅读建议：先读 Top 3 长文，再重点关注看涨/看跌论据。"
    )

    section = f"""

---

## 🎀 卡皮观点（自动生成 · {now}）

> 本节由 `deep_dive.reporting.capy_summary` 追加。
> 读 report.md 全文提取主题词、关键引用、多空论据。**不调用 LLM**。
> 如需更深度的"带主观判断"综述，请主进程在卡皮 LLM 上下文里读 `report.md` 自行撰写 `卡皮综述.md`。

### 📊 抓取元数据摘要

| 指标 | 数值 |
|------|------|
| 总 URL | {total} |
| 成功 | {n_success} ({n_success * 100 // max(total, 1)}%) |
| 拦截 | {n_blocked} |
| 失败 | {n_failed} |
| 中文来源 | {lang_counter.get('中文', 0)} |
| 英文来源 | {lang_counter.get('英文', 0)} |
| 搜索任务数 | {len(task_results)} |
| 报告文章数 | {cv.get('article_count', 0)} |

### 🔍 搜索任务覆盖

{task_block}
{failed_block}
### 🏆 抓取字数 Top 5 URL

{top5_block}

### 📝 内容主题归纳

**高频关键短语**（Top 10）：

{phrase_block}

**多空博弈 + 事实数据**：

{themed_block}

**关键引用**：

{quote_block}

### 💡 三个内容观点

{capy_block}

### ⚡ {one_liner}

---

## 📋 主进程下一步建议

1. **精读**：从 Top 5 URL 选 2-3 个最相关的深入阅读
2. **多空交叉**：对比「看涨论据」与「看跌论据」两边素材
3. **补抓**（如需）：用 `python -m deep_dive.crawler.engines.mmx --query "..."` 针对被拦截的 `baike.baidu.com` 等单独深抓
4. **写书素材**：`report.md` 已按内容长度排序，可直接喂给 create-science-book 技能

"""
    return section


def _build_empty_section(query: str, task_results: list[TaskResult], reason: str) -> str:
    """Build a short status block when there's no content."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_quota = sum(1 for r in task_results if r.status == TaskStatus.QUOTA_EXCEEDED)
    if n_quota > 0:
        status_tag = "[QUOTA]"
        hint = (
            "本轮多个 task 触发 MMX 配额耗尽。建议：等 4h 后重试、"
            "或减 `--depth=quick`、或加 `--no-tavily` 避免双重消耗。"
        )
    else:
        status_tag = "[EMPTY]"
        hint = (
            "本轮未抓取到任何有效内容。建议：1) 检查 query 是否太泛；"
            "2) 检查 `raw/` 目录下 `metadata.json` 诊断；"
            "3) 换 `--depth=full` 或重写查询词重试。"
        )
    return f"""

---

## 🎀 卡皮观点（自动生成 · {now}）

> **⚠️ {status_tag} 数据不足** — {reason}
> {hint}
> **不生成三个观点，避免幻觉输出。**

"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_capy_section(
    *,
    report_path: Path,
    query: str,
    task_results: list[TaskResult],
    aggregated_meta: list[FetchResult],
) -> bool:
    """Append the Capy summary section to ``report_path``.

    Args:
        report_path: the ``report.md`` to append to.
        query: original search query.
        task_results: results from each matrix row.
        aggregated_meta: list of all :class:`FetchResult` (any status).

    Returns:
        True if the section was written; False on error (never raises).
    """
    try:
        report_path = Path(report_path)
        if not report_path.exists():
            safe_print(f"{TAG_CFY} report.md not found: {report_path}")
            return False

        content = report_path.read_text(encoding="utf-8", errors="ignore")

        # Remove old section (idempotent re-runs)
        marker = "## 🎀 卡皮观点"
        if marker in content:
            idx = content.find(marker)
            content = content[:idx].rstrip() + "\n"
            safe_print(f"{TAG_CFY} removed old section (kept {idx} chars)")

        total = len(aggregated_meta) if aggregated_meta else 0
        n_success = sum(1 for m in (aggregated_meta or []) if m.status == FetchStatus.SUCCESS)

        if total == 0 or n_success == 0:
            new_section = _build_empty_section(query, task_results, reason=f"本轮抓取 {total} 个独立 URL，成功 {n_success}")
        else:
            new_section = _build_section(query, task_results, aggregated_meta, report_text=content)

        final = content + new_section
        report_path.write_text(final, encoding="utf-8", errors="ignore")
        safe_print(f"{TAG_CFY} appended ({len(new_section)} chars) to {report_path}")
        return True
    except Exception as e:
        safe_print(f"{TAG_CFY} append failed: {type(e).__name__}: {e}")
        return False


__all__ = ["append_capy_section", "detect_lang_from_url"]
