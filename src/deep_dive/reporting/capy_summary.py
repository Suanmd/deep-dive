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
_PARAGRAPH_RE = re.compile(r"\n\s*\n")  # split on blank lines

# Regex helpers reused for paragraph quality scoring.
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_MONEY_RE = re.compile(
    r"(?:¥|￥|\$|€|£|USD|CNY|HKD|TWD)\s*\d+(?:[.,]\d+)*"
    r"|\d+(?:[.,]\d+)*\s*(?:亿|万亿|百万|千万|万|billion|million|thousand)",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"\d+(?:\.\d+)?%")


# ---------------------------------------------------------------------------
# Topic clustering
# ---------------------------------------------------------------------------
# When the query is a multi-meaning acronym (e.g. "OPD/MOPD" → AI
# On-Policy Distillation vs Maryland Public Defender vs Pennsylvania
# Office of Developmental Programs vs NYC Mayor's Office for People
# with Disabilities vs Operational Psychodynamic Diagnostics), the
# sentiment-only buckets (bull/bear/fact) lose semantic context. The
# reader sees a paragraph about Maryland legal training mixed with one
# about arxiv 2605.12652 and has no signal which interpretation
# dominates.
#
# Fix: assign each paragraph a topic label based on the source URL's
# host (primary signal) plus a few in-text disambiguating tokens
# (secondary signal for paragraphs that mention multiple topics).
# Then group paragraphs by topic and render a dedicated "按主题分组"
# section in the capy output.

_TOPIC_HOST_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Order matters: first match wins. So put the most-specific
    # (longest) hosts first.
    (
        "ai_ml",
        (
            "arxiv.org",
            "github.com",
            "paperswithcode.com",
            "huggingface.co",
            "verl.readthedocs.io",
            "nvidia.com",
            "anthropic.com",
            "openai.com",
            "deepmind.google",
            "deepseek.ai",
            "bytedtsinghua-sia.github.io",
            "baai.ac.cn",
            "emergentmind.com",
        ),
    ),
    (
        "legal_training",
        (
            "opd.state.md.us",
            "state.md.us",
            "mdcourts.gov",
        ),
    ),
    (
        "disability_services",
        (
            "paautism.org",
            "myodp.org",
            "earlyconnections.mo.gov",
            "nyc.gov",  # NYC MOPD
        ),
    ),
    (
        "psychology",
        (
            "ablesky.com",
            "iap.edu.au",
        ),
    ),
    (
        "medical_clinical",
        (
            "livderm.org",
            "rdcacademy.com",
        ),
    ),
)


def _topic_for_url(url: str) -> str:
    """Map a source URL to a coarse topic bucket.

    Returns one of: ``"ai_ml"``, ``"legal_training"``,
    ``"disability_services"``, ``"psychology"``, ``"medical_clinical"``,
    or ``"other"`` when no host hint matches.
    """
    if not url:
        return "other"
    low = url.lower()
    for topic, hosts in _TOPIC_HOST_HINTS:
        if any(h in low for h in hosts):
            return topic
    return "other"


# In-text disambiguating tokens (Chinese + English). Used as a
# secondary signal: if the URL host doesn't match any topic but the
# paragraph text contains one of these tokens, we re-classify based
# on the token. This catches mirror sites and re-blogged content.
_TOPIC_TEXT_HINTS: dict[str, tuple[str, ...]] = {
    "ai_ml": (
        "on-policy",
        "distillation",
        "rlhf",
        "reward model",
        "policy gradient",
        "multi-teacher",
        "arxiv",
        "neural network",
        "transformer",
        "llm",
        "智能体",
        "蒸馏",
        "强化学习",
        "大模型",
        "预训练",
    ),
    "legal_training": (
        "public defender",
        "attorney",
        "lawyer",
        "法庭",
        "辩护",
        "律师",
        "刑事",
    ),
    "disability_services": (
        "developmental programs",
        "intellectual disability",
        "autism",
        "残疾",
        "智障",
        "自闭症",
        "developmental disability",
    ),
    "psychology": (
        "psychodynamic",
        "psychoanalytic",
        "心理动力学",
        "心理咨询",
        "OPD-2",
    ),
    "medical_clinical": (
        "pediatric dermatology",
        "skin disease",
        "皮肤科",
        "湿疹",
        # Substance use / addiction (OUD/MOUD/SAMHSA etc.). Without
        # these, queries like "OPD training" route OUD medical content
        # to "other" bucket because the URL hosts (who.int, samhsa.gov)
        # aren't in the host-hint table. v3 regression showed
        # "Creating Optimal Access for Opioid Use Disorder" landing
        # in "其他" — should be medical.
        "opioid",
        "oud",
        "moud",
        "substance use",
        "substance abuse",
        "addiction",
        "withdrawal",
        "mat ",
        "samhsa",
    ),
}


def _refine_topic_by_text(paragraph: str, current_topic: str) -> str:
    """Re-classify a paragraph based on in-text disambiguating tokens.

    Only re-classifies AWAY from ``"other"`` — we don't override the
    URL-host signal when it's already specific. This prevents a
    paragraph from a Maryland legal site that happens to mention
    "training" from being pulled into AI/ML.
    """
    if current_topic != "other" or not paragraph:
        return current_topic
    low = paragraph.lower()
    # Count hits per topic; pick the one with most hits.
    hits: dict[str, int] = {}
    for topic, tokens in _TOPIC_TEXT_HINTS.items():
        n = sum(1 for t in tokens if t.lower() in low)
        if n > 0:
            hits[topic] = n
    if not hits:
        return "other"
    best_topic = max(hits.items(), key=lambda kv: kv[1])[0]
    return best_topic


_AUTHORITATIVE_HOST_HINTS = (
    # Each entry: (host_substring, score_weight). Higher = more authoritative.
    ("wikipedia.org", 15),
    ("sec.gov", 18),
    ("ft.com", 14),
    ("statista.com", 14),
    ("reuters.com", 13),
    ("bloomberg.com", 13),
    ("forbes.com", 11),
    ("36kr.com", 10),  # 36 氪 — China tech-business primary
    ("tmtpost.com", 10),  # 钛媒体
    ("21jingji.com", 10),  # 21 财经
    ("businessofapps.com", 12),
    ("corpdigest.com", 8),
    ("github.com", 9),
    ("arxiv.org", 14),
    ("paperswithcode.com", 10),
)


def _source_authority_boost(url_or_source: str) -> int:
    """Heuristic authority score (0-20) from a URL or source-task label.

    Used as one component of paragraph quality scoring. We don't have
    a real authority database; the host-substring table above is a
    best-effort shortcut. Articles from unknown hosts get +0.
    """
    if not url_or_source:
        return 0
    low = url_or_source.lower()
    for hint, weight in _AUTHORITATIVE_HOST_HINTS:
        if hint in low:
            return weight
    return 0


def _paragraph_quality_score(text: str, source: str) -> dict[str, Any]:
    """Score a single paragraph for "view-worthy" inclusion.

    Three components, summed:

    1. **Length** — 0-25 pts. Caps at 500 chars (longer paragraphs
       don't get more points; they risk being too dense to quote).
    2. **Data density** — 0-45 pts. Counts numbers, currency, %.
       Money/percent get 2x weight (they carry more semantic load).
    3. **Source authority** — 0-20 pts. See
       :func:`_source_authority_boost`.

    Total range: 0-90 (we keep headroom above the max because future
    axes may add up to 10 more pts).
    """
    if not text:
        return {"score": 0, "length_pts": 0, "data_pts": 0, "auth_pts": 0}

    char_count = len(text)
    length_pts = min(25, int(char_count / 20))  # 500 chars → 25 pts

    digits = len(_NUM_RE.findall(text))
    money = len(_MONEY_RE.findall(text))
    pcts = len(_PCT_RE.findall(text))
    data_pts = min(45, digits * 2 + money * 5 + pcts * 4)

    auth_pts = _source_authority_boost(source)

    total = length_pts + data_pts + auth_pts
    return {
        "score": total,
        "length_pts": length_pts,
        "data_pts": data_pts,
        "auth_pts": auth_pts,
    }


def _classify_paragraph(text: str) -> set[str]:
    """Classify a paragraph into one or more categories.

    Returns a set containing any of: ``"predictive"``, ``"bull"``,
    ``"bear"``, ``"fact"``, ``"context"``. A paragraph can belong to
    multiple (e.g. "TikTok 2025 营收预计 $33.1B (+40%)" is both
    predictive AND fact).
    """
    cats: set[str] = set()
    # Multi-keyword threshold: a single "增长" word shouldn't be enough
    # to flip a paragraph into "bull". Require 2+ keyword hits OR
    # 1 hit AND a number present.
    hits = {"predictive": 0, "bull": 0, "bear": 0}
    for kw in PREDICTIVE_KEYWORDS:
        if kw in text:
            hits["predictive"] += 1
    for kw in BULL_KEYWORDS:
        if kw in text:
            hits["bull"] += 1
    for kw in BEAR_KEYWORDS:
        if kw in text:
            hits["bear"] += 1

    has_number = bool(re.search(r"\d{2,}", text))
    threshold = 2 if not has_number else 1
    for cat, n in hits.items():
        if n >= threshold:
            cats.add(cat)

    if has_number and len(text) >= 50:
        cats.add("fact")

    if not cats:
        cats.add("context")
    return cats


def _split_paragraphs(body: str) -> list[str]:
    """Split an article body into paragraphs."""
    if not body:
        return []
    # Strip code-fence markers first so they don't get treated as
    # paragraph content. Markdown ``` on its own line is a fence, not
    # a sentence.
    cleaned = re.sub(r"^```[a-z]*\s*$", "", body, flags=re.MULTILINE)
    # Strip **URL**: and **来源**: metadata lines (these are
    # injected by reporting.builder.build_report).
    cleaned = re.sub(r"^\*\*URL\*\*:.*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\*\*来源\*\*:.*$", "", cleaned, flags=re.MULTILINE)
    parts = [p.strip() for p in _PARAGRAPH_RE.split(cleaned) if p.strip()]
    if len(parts) < 2:
        parts = [p.strip() for p in cleaned.split("\n") if p.strip() and len(p.strip()) >= 25]
    return [p for p in parts if len(p) >= 25 and not p.startswith("```")]


def _extract_content_views(report_text: str, query: str, max_chars_per_article: int = 800) -> dict[str, Any]:
    """Extract thematic views from a fully-built ``report.md``.

    * **Article-level segmentation first** — parse ``### N. title``
      sections to know which article each piece of content came from.
    * **Paragraph-level extraction** (was: sentence-level). Whole
      paragraphs preserve argument context; sentence fragments could
      lose the "why" of a claim.
    * **Quality scoring per paragraph** — length, data density, source
      authority. Top-N-per-category by score, NOT first-N (was the
      root cause of the previous "missed 钛媒体 / 腾讯新闻 analysis"
      symptom: the first paragraph happened to be a generic intro).
    * **Source attribution** — every quoted chunk is tagged with its
      parent article so the reader can audit the source.

    Args:
        report_text: full report body (UTF-8).
        query: the user's query (used to filter relevant phrases).
        max_chars_per_article: cap per article body when scanning.

    Returns:
        Dict with keys:
        - ``article_count``: number of ``### N. title`` sections parsed
        - ``key_phrases``: top 20 (phrase, count) pairs
        - ``themed_clusters``: dict of category → list[dict] where
          each dict has ``text``, ``source``, ``score``.
        - ``key_quotes``: top 5 numeric / quoted paragraphs
        - ``high_chars_articles``: top 5 articles by char count
        - ``source_authority``: top 5 most-authoritative URLs seen
    """
    if not report_text or not query:
        return {}

    matches = list(_ARTICLE_RE.finditer(report_text))
    if not matches:
        return {}

    articles: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        # Skip report builder's appendix headers (low-quality page list,
        # "dropped tasks" section, etc.). These aren't real articles —
        # they're report-rendering metadata that gets captured by the
        # `### title` regex. Without this filter, low-quality bullet
        # lists pollute the capy paragraph pool.
        if title.startswith(("⚠️", "❗", "❌", "🗑️", "[skip]", "Cap ", "Dropped")):
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        body = report_text[start:end].strip()
        # Pull URL from the body if present ("**URL**: ...") so we can
        # use it for source-authority scoring.
        url_m = re.search(r"\*\*URL\*\*:\s*(\S+)", body)
        url = url_m.group(1) if url_m else ""
        articles.append(
            {
                "title": title,
                "body": body[:max_chars_per_article],
                "full_chars": len(body),
                "url": url,
            }
        )

    # --- Build keyword set from query (for filtering) ---------------
    query_kws: set[str] = set()
    for c in query:
        if "\u4e00" <= c <= "\u9fff":
            query_kws.add(c)
    for w in _EN_WORD_RE.findall(query.lower()):
        query_kws.add(w.lower())

    # --- Phrase counting -------------------------------------------
    counter: Counter[str] = Counter()
    MAX_EN_PHRASE_LEN = 12
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
            # Drop OCR garbage / concatenated tokens
            if len(wl) > MAX_EN_PHRASE_LEN:
                continue
            if not any(kw in wl for kw in query_kws):
                continue
            counter[wl] += 1
    top_phrases = counter.most_common(20)

    # --- Paragraph extraction + scoring ----------------------------
    # One flat list of (paragraph_text, source_title, source_url,
    # category_set, quality_dict). We then bucket into themed_clusters
    # by category, sort by score, and cap per-bucket.
    all_paragraphs: list[dict[str, Any]] = []
    for art in articles:
        # Compute topic for this article from its URL host. This is
        # the primary signal for topic clustering.
        article_topic = _topic_for_url(art["url"])
        for para in _split_paragraphs(art["body"]):
            quality = _paragraph_quality_score(para, art["url"] or art["title"])
            cats = _classify_paragraph(para)
            # Secondary signal: refine topic based on in-text hints
            # ONLY when the URL didn't match any known topic.
            para_topic = _refine_topic_by_text(para, article_topic)
            all_paragraphs.append(
                {
                    "text": para,
                    "source": art["title"][:50],
                    "url": art["url"],
                    "categories": cats,
                    "quality": quality,
                    "topic": para_topic,
                }
            )

    # Drop URL-residue lines (rare, but legacy build_report can leak
    # **URL**: https://... into the body if a parser bug ever surfaces).
    all_paragraphs = [
        p for p in all_paragraphs if not re.search(r"^https?://|^/?/?[a-z]+/[a-z]+/", p["text"].strip())
    ]

    # --- Themed clustering by score --------------------------------
    themed: dict[str, list[dict[str, Any]]] = {
        "predictive": [],
        "bull": [],
        "bear": [],
        "fact": [],
        "context": [],
    }
    for p in all_paragraphs:
        for cat in p["categories"]:
            themed[cat].append(p)

    # Dedupe (same paragraph text shouldn't appear twice) and sort by
    # quality score descending. Cap each cluster to top 5 (was 3).
    for cat, items in themed.items():
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        # Sort first so the highest-quality survives dedup.
        items.sort(key=lambda p: p["quality"]["score"], reverse=True)
        for p in items:
            if p["text"] in seen:
                continue
            seen.add(p["text"])
            unique.append(p)
            if len(unique) >= 5:
                break
        themed[cat] = unique

    # --- Key quotes: top paragraphs by quality (any category) ------
    quotes: list[dict[str, Any]] = []
    seen_q: set[str] = set()
    by_score = sorted(all_paragraphs, key=lambda p: p["quality"]["score"], reverse=True)
    for p in by_score:
        text = p["text"]
        if text in seen_q:
            continue
        if len(text) < 50:
            continue
        # Require at least one number OR a quoted string OR >=80 chars
        # of policy language.
        has_signal = (
            bool(re.search(r"\d+[%美元/吨/盎司/倍]|\d{4,}|\d+\.\d+", text))
            or '"' in text
            or "“" in text
            or len(text) >= 120
        )
        if not has_signal:
            continue
        seen_q.add(text)
        quotes.append({"text": text[:240], "source": p["source"], "score": p["quality"]["score"]})
        if len(quotes) >= 5:
            break

    high_chars = sorted(articles, key=lambda a: a["full_chars"], reverse=True)[:5]
    high_chars_summary = [(a["title"][:50], a["full_chars"], a["url"][:60]) for a in high_chars]

    # Authority roll-up: most-authoritative URLs seen in the corpus.
    auth_seen: dict[str, int] = {}
    for art in articles:
        if art["url"]:
            score = _source_authority_boost(art["url"])
            if score > 0:
                auth_seen[art["url"]] = max(auth_seen.get(art["url"], 0), score)
    source_authority = sorted(auth_seen.items(), key=lambda kv: -kv[1])[:5]

    # --- Topic clustering --------------------------
    # Group paragraphs by topic (derived from URL host + in-text hints).
    # Each topic shows its top-N paragraphs by quality score. This
    # surfaces semantic structure when the query is a multi-meaning
    # acronym (OPD = AI / Maryland legal / PA disability / psychology /
    # medical etc.) — readers see "X is dominated by AI/ML" vs
    # "Y is mostly Maryland legal training" at a glance.
    topic_groups: dict[str, list[dict[str, Any]]] = {}
    for p in all_paragraphs:
        topic_groups.setdefault(p.get("topic", "other"), []).append(p)
    # Sort + cap each topic group.
    for topic, items in topic_groups.items():
        topic_seen: set[str] = set()
        items.sort(key=lambda p: p["quality"]["score"], reverse=True)
        topic_unique: list[dict[str, Any]] = []
        for p in items:
            if p["text"] in topic_seen:
                continue
            topic_seen.add(p["text"])
            topic_unique.append(p)
            if len(topic_unique) >= 4:  # cap at 4 paragraphs per topic
                break
        topic_groups[topic] = topic_unique
    # Sort topics by paragraph count desc, so the dominant topic
    # surfaces first.
    topic_groups_sorted = dict(sorted(topic_groups.items(), key=lambda kv: -len(kv[1])))

    return {
        "article_count": len(articles),
        "key_phrases": top_phrases,
        "themed_clusters": themed,
        "key_quotes": quotes,
        "high_chars_articles": high_chars_summary,
        "source_authority": source_authority,
        "topic_groups": topic_groups_sorted,
    }


# ---------------------------------------------------------------------------
# Section builder
# ---------------------------------------------------------------------------


def _build_section(
    query: str, task_results: list[TaskResult], aggregated_meta: list[FetchResult], report_text: str = ""
) -> str:
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
        key=lambda m: m.chars,
        reverse=True,
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
            failed_lines.append(f"| {tr.note} | {tr.status.value} | {error} | {engine} | {degraded} |")
    task_block = "\n".join(task_lines) if task_lines else "  - (无 task 元数据)"

    # Build a dedicated failure-listing block. Hidden when
    # all tasks succeeded; explicit table when any task failed.
    failed_block = ""
    if failed_lines:
        failed_block = (
            "\n### ⚠️ 失败任务清单\n\n"
            "| 任务 | 状态 | 错误 | 引擎 | 降级 |\n"
            "|------|------|------|------|------|\n" + "\n".join(failed_lines) + "\n"
        )

    cv = _extract_content_views(report_text or "", query)

    # --- themed block: paragraphs + source attribution ---
    if cv.get("themed_clusters"):
        tc = cv["themed_clusters"]
        themed_block_lines: list[str] = []
        # Render each category as a list of paragraph entries with
        # source attribution. Format: "[来源: 文章标题] 段落片段…"
        # capped to 200 chars per chunk so the section stays scannable.
        for theme_name, label in [
            ("predictive", "预测 / 机构观点"),
            ("bull", "看涨论据"),
            ("bear", "看跌论据"),
            ("fact", "事实 / 数据"),
            ("context", "背景 / 上下文"),
        ]:
            items = tc.get(theme_name, [])
            if not items:
                continue
            themed_block_lines.append(f"\n**{label}** ({len(items)} 条，按质量评分排序):")
            for it in items:
                chunk = it["text"][:200]
                src = it.get("source", "未知来源")
                score = it.get("quality", {}).get("score", 0)
                themed_block_lines.append(f"  - [来源: {src}] (评分 {score}) {chunk}")
        themed_block = "\n".join(themed_block_lines) or "\n(未读出内容主题分类)\n"
    else:
        themed_block = "\n(未读出内容主题分类)\n"

    if cv.get("key_phrases"):
        phrase_block = "\n".join(f"  - `{p}` x {c}" for p, c in cv["key_phrases"][:10])
    else:
        phrase_block = "  (无)"

    # Quotes now carry source attribution and quality score.
    if cv.get("key_quotes"):
        quote_lines = []
        for q in cv["key_quotes"][:5]:
            quote_lines.append(f"\n  > {q['text']}")
            quote_lines.append(f"  > &nbsp;&nbsp;_[来源: {q.get('source', '?')}, 评分 {q.get('score', 0)}]_")
        quote_block = "\n".join(quote_lines) or "\n  (无)\n"
    else:
        quote_block = "\n  (无)\n"

    # Topic groups. When the corpus has multiple
    # semantic interpretations (e.g. OPD = AI / Maryland legal / PA
    # disability / psychology), this surfaces the breakdown so the
    # reader knows which interpretation dominates without scanning
    # the whole §3 body.
    _TOPIC_LABELS = {
        "ai_ml": "🧠 AI / ML",
        "legal_training": "⚖️ 法律 / 司法培训",
        "disability_services": "♿ 残障 / 发展性服务",
        "psychology": "🧩 心理动力学",
        "medical_clinical": "🩺 医学 / 临床",
        "other": "📂 其他",
    }
    if cv.get("topic_groups"):
        tg = cv["topic_groups"]
        topic_block_lines = ["**按主题分组** (corpus 多语义时定位主导主题):", ""]
        for topic_key, items in tg.items():
            label = _TOPIC_LABELS.get(topic_key, topic_key)
            topic_block_lines.append(f"\n**{label}** ({len(items)} 条):")
            for it in items:
                chunk = it["text"][:160]
                src = it.get("source", "?")[:50]
                score = it.get("quality", {}).get("score", 0)
                topic_block_lines.append(f"  - [来源: {src}] (评分 {score}) {chunk}")
        topic_block = "\n".join(topic_block_lines)
    else:
        topic_block = ""

    # Authority roll-up (new section). Shows which authoritative
    # sources actually contributed, so the reader knows whether
    # claims come from primary hubs or only from secondary aggregators.
    if cv.get("source_authority"):
        auth_lines = []
        for url, score in cv["source_authority"]:
            auth_lines.append(f"  - `{url[:70]}` (权威分 {score})")
        authority_block = "\n".join(auth_lines)
    else:
        authority_block = "  (本轮抓取未触及预设权威站点)"

    n_pred = len(cv.get("themed_clusters", {}).get("predictive", []))
    n_bull = len(cv.get("themed_clusters", {}).get("bull", []))
    n_bear = len(cv.get("themed_clusters", {}).get("bear", []))
    n_fact = len(cv.get("themed_clusters", {}).get("fact", []))
    n_context = len(cv.get("themed_clusters", {}).get("context", []))

    # Sentiment balance from paragraph counts (was: keyword-match
    # count). Each category = a paragraph, so the ratio approximates
    # "how much of the corpus argues bull vs bear".
    total_directional = max(n_bull + n_bear, 1)
    bull_share = n_bull / total_directional
    bear_share = n_bear / total_directional
    if bull_share > 0.6:
        sentiment = "看涨论据压倒性（>60%）"
    elif bear_share > 0.6:
        sentiment = "看跌论据压倒性（>60%）"
    elif n_bull > n_bear:
        sentiment = f"看涨偏多（{n_bull} vs {n_bear}）"
    elif n_bear > n_bull:
        sentiment = f"看跌偏多（{n_bear} vs {n_bull}）"
    else:
        sentiment = "多空平衡"

    top_phrase = cv.get("key_phrases", [("", 0)])[0][0] if cv.get("key_phrases") else "N/A"
    views = [
        f"**观点 1（内容主题）**：从 report.md {cv.get('article_count', 0)} 个文章 section 抽取 {sum(1 for _ in cv.get('key_phrases', []))} 个高频关键短语，Top 5：`{', '.join(f'{p}({c})' for p, c in cv.get('key_phrases', [])[:5])}` —— 本轮主题焦点。",
        f"**观点 2（多空博弈）**：预测 {n_pred} 条 / 看涨 {n_bull} 条 / 看跌 {n_bear} 条 / 事实 {n_fact} 条 / 背景 {n_context} 条。{sentiment}。",
        f"**观点 3（数据质量）**：抓取 {total} 个独立 URL（成功 {n_success}），"
        f"中文 {lang_counter.get('中文', 0)} 个 / 英文 {lang_counter.get('英文', 0)} 个。"
        f"信噪比最高的 Top 3 长文：`{' / '.join(m.title[:30] for m in top5[:3])}`。"
        + (
            f" 触及 {len(cv.get('source_authority', []))} 个预设权威源。"
            if cv.get("source_authority")
            else " 未触及预设权威源（需检查 target_sites 配置）。"
        ),
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
>
> 论点抽取升级到段落级（保留上下文），每条带来源归属和质量评分。信息密度评分替代纯字数阈值。
>
> 多义词 corpus 加按主题分组（AI/ML vs 法律 vs 残障服务 vs 心理学 vs 医学）。

### 📊 抓取元数据摘要

| 指标 | 数值 |
|------|------|
| 总 URL | {total} |
| 成功 | {n_success} ({n_success * 100 // max(total, 1)}%) |
| 拦截 | {n_blocked} |
| 失败 | {n_failed} |
| 中文来源 | {lang_counter.get("中文", 0)} |
| 英文来源 | {lang_counter.get("英文", 0)} |
| 搜索任务数 | {len(task_results)} |
| 报告文章数 | {cv.get("article_count", 0)} |

### 🔍 搜索任务覆盖

{task_block}
{failed_block}
### 🏆 抓取字数 Top 5 URL

{top5_block}

### 📝 内容主题归纳

**高频关键短语**（Top 10）：

{phrase_block}

{topic_block}

**多空博弈 + 事实数据**（每条带来源 + 质量评分）：

{themed_block}

**关键引用**（Top 5，含来源）：

{quote_block}

**权威源覆盖**（命中预设权威站点的 URL）：

{authority_block}

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
            new_section = _build_empty_section(
                query, task_results, reason=f"本轮抓取 {total} 个独立 URL，成功 {n_success}"
            )
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
