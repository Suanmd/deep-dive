"""Three-stage query relevance check (logic).

Why three stages?
-----------------

The legacy single-char density check (``单字密度 ≥ 25%``) had a known
failure mode: aliyun nav pages (10万字 menu) hit 40% on the character
``存/储/能/产`` even though no "长鑫" / "DRAM" core entity was present.
The fix is the multi-stage verification:

    Stage 1: Single-char density ≥ ``QUERY_RELEVANCE_MIN_HITRATE``
             (default 25%). Catches the obvious "no overlap at all" case.
    Stage 2: Core-entity coverage ≥ ``QUERY_CORE_ENTITY_MIN_HITRATE``
             (default 34%). Catches the "high character density but
             wrong topic" case.
    Stage 3: Primary entity must appear in the document lead (first
             ``PRIMARY_LEAD_FRAC`` of the text, min 500 chars). Catches
             the "topic-drift" failure mode where a query lists several
             related concepts and a tangentially-related article happens
             to mention one of them (e.g. a query "费马大定理 朗兰兹
             纲领" matching a 2024 Geometric Langlands breakthrough
             article that mentions FLT only as a historical example).
             Any stage failing → text is judged **irrelevant** and dropped.

The threshold values are configurable via :class:`deep_dive.config.Config`
so users with different corpus characteristics can tune them.
"""

from __future__ import annotations

import re

from deep_dive.constants import (
    QUERY_CORE_ENTITY_MIN_HITRATE,
    QUERY_RELEVANCE_MIN_HITRATE,
)
from deep_dive.types import RelevanceVerdict

# Pre-compiled regexes (avoid recompiling on every call)
_CN_CHAR = re.compile(r"[\u4e00-\u9fff]")
_CN_ENTITY = re.compile(r"[\u4e00-\u9fff]{2,}")
_EN_ENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,}")
_EN_WORD = re.compile(r"[A-Za-z]{3,}")


# Primary-entity lead check (topic-drift guard).
# When a query lists multiple related concepts (e.g. "费马大定理 谷山
# 志村 朗兰兹纲领") the two-stage check can match a tangentially-related
# article that *mentions* one concept in passing. Stage 3 requires the
# query's primary entity to appear in the document lead, otherwise the
# article is treated as a topic-drift false-positive.
PRIMARY_LEAD_FRAC: float = 0.3
PRIMARY_MIN_LEN: int = 3  # require ≥3 chars to avoid generic verbs like "证明"


def _query_keywords(query: str) -> set[str]:
    """Extract all keywords from ``query``.

    Returns:
        A set of unique keywords: each Chinese character is a separate
        keyword; each English word of length ≥ 3 is a single keyword.
    """
    out: set[str] = set()
    if not query:
        return out
    for c in query:
        if "\u4e00" <= c <= "\u9fff":
            out.add(c)
    for w in _EN_WORD.finditer(query):
        out.add(w.group(0).lower())
    return out


def query_keyword_density(text: str, query: str) -> float:
    """Compute the keyword-hit ratio of ``text`` w.r.t. ``query``.

    The keywords are extracted from ``query`` (see :func:`_query_keywords`).
    For each keyword, check if it appears in ``text`` (case-insensitive).
    The result is ``hits / total_keywords``.

    Args:
        text: candidate document text (already extracted plain text).
        query: the query the document is being judged against.

    Returns:
        Float in ``[0.0, 1.0]``. If the query has no keywords
        (empty / no Chinese chars / no English 3+ words), returns
        ``1.0`` (vacuous pass).

    Examples:
        >>> query_keyword_density("黄金价格飙升", "黄金投资")
        1.0
        >>> query_keyword_density("Microsoft Visual Studio 18.3 release notes", "黄金投资")
        0.0
        >>> query_keyword_density("", "黄金投资")
        0.0
    """
    if not text or not query:
        return 0.0
    kws = _query_keywords(query)
    if not kws:
        return 1.0
    low = text.lower()
    hits = sum(1 for kw in kws if kw.lower() in low)
    return hits / len(kws)


def _extract_core_entities(query: str) -> list[str]:
    """Extract core entities (2+ Chinese chars, 3+ alphanum English tokens).

    Examples:
        >>> _extract_core_entities("长鑫 DRAM 2026")
        ['长鑫', 'dram', '2026']
        >>> _extract_core_entities("hello world foo")
        ['hello', 'world', 'foo']

    For Chinese runs of 4+ characters, we also emit the two halves split
    at the midpoint. This handles the common case where ``黄金价格 持续
    上涨 投资价值凸显`` contains the words ``黄金`` and ``投资`` from
    the query ``黄金投资`` but not the whole query as a substring.
    Without the half-split the whole-string greedy match would falsely
    miss the page as irrelevant.
    """
    if not query:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _CN_ENTITY.finditer(query):
        s = m.group(0)
        if s not in seen:
            seen.add(s)
            out.append(s)
        # Bisect long Chinese runs to expose their internal word boundaries.
        # E.g. "黄金投资" (4 chars) → also try "黄金" and "投资".
        if len(s) >= 4:
            mid = len(s) // 2
            left, right = s[:mid], s[mid:]
            if len(left) >= 2 and left not in seen:
                seen.add(left)
                out.append(left)
            if len(right) >= 2 and right not in seen:
                seen.add(right)
                out.append(right)
    for m in _EN_ENTITY.finditer(query):
        ml = m.group(0).lower()
        if ml not in seen:
            seen.add(ml)
            out.append(ml)
    return out


def core_entity_hitrate(text: str, query: str) -> tuple[int, int]:
    """Count how many of ``query``'s core entities appear in ``text``.

    Returns:
        Tuple ``(hits, total)``. ``total`` may be ``0`` if the query
        has no extractable entities; in that case the tuple is
        ``(0, 0)`` and the caller is expected to treat that as "pass".

    Examples:
        >>> core_entity_hitrate("长鑫存储产能激增", "长鑫存储 DRAM")
        (1, 2)
        >>> core_entity_hitrate("Microsoft Office 365 pricing", "长鑫 DRAM")
        (0, 2)
    """
    entities = _extract_core_entities(query)
    if not entities:
        return (0, 0)
    low = text.lower()
    hits = sum(1 for e in entities if e.lower() in low)
    return (hits, len(entities))


def _extract_primary_entity(query: str, *, min_len: int = PRIMARY_MIN_LEN) -> str | None:
    """Extract the query's primary entity for the topic-drift guard.

    Picks the *first* Chinese 2+ char run whose length is ≥ ``min_len``.
    Shorter entities (e.g. the 2-char verb ``证明``) are skipped because
    they're too generic to anchor the topic.

    Returns:
        The primary entity string, or ``None`` if none qualifies
        (English-only queries, very short queries, etc.). A ``None``
        primary causes stage 3 to pass vacuously.

    Examples:
        >>> _extract_primary_entity("费马大定理 证明 怀尔斯 谷山志村")
        '费马大定理'
        >>> _extract_primary_entity("证明 费马大定理")  # order-independent
        '费马大定理'
        >>> _extract_primary_entity("费马")  # below min_len (3)
        >>> _extract_primary_entity("DRAM market 2026")  # no Chinese entities
        >>> _extract_primary_entity("")  # empty query
    """
    if not query:
        return None
    for m in _CN_ENTITY.finditer(query):
        if len(m.group(0)) >= min_len:
            return m.group(0)
    return None


def _primary_in_lead(
    text: str,
    primary: str,
    *,
    lead_frac: float = PRIMARY_LEAD_FRAC,
) -> bool:
    """Check whether ``primary`` appears in the lead of ``text``.

    The lead is ``max(500, int(len(text) * lead_frac))`` characters. The
    500-char floor protects against ultra-short articles being judged
    by a tiny fraction of their content.

    To stay symmetric with the bisect heuristic in
    :func:`_extract_core_entities`, if ``primary`` is ≥4 chars and the
    full entity isn't in the lead, we also accept either half (so
    queries like "黄金投资" still match documents that use "黄金" and
    "投资" separately).

    Args:
        text: document text.
        primary: primary entity (from :func:`_extract_primary_entity`).
        lead_frac: fraction of text considered "lead" (default 30%).

    Returns:
        True if ``primary`` (or either half of it, if ≥4 chars) appears
        in the lead. Vacuously True if ``text`` or ``primary`` is empty.

    Examples:
        >>> _primary_in_lead("黄金价格 上涨 投资价值凸显", "黄金投资")
        True
        >>> _primary_in_lead("..." * 1000 + " 费马大定理" + "..." * 1000, "费马大定理")
        False
    """
    if not text or not primary:
        return True
    cutoff = max(500, int(len(text) * lead_frac))
    lead = text[:cutoff]
    if primary in lead:
        return True
    # Mirror of _extract_core_entities bisect: accept either half.
    # (Both halves of "费马大定理" can't appear in lead if the full
    #  entity doesn't, so this only helps cases like "黄金投资" where
    #  the halves appear separately.)
    if len(primary) >= 4:
        mid = len(primary) // 2
        if primary[:mid] in lead or primary[mid:] in lead:
            return True
    return False


def is_query_irrelevant(
    text: str,
    query: str,
    *,
    min_hitrate: float = QUERY_RELEVANCE_MIN_HITRATE,
    core_entity_min_hitrate: float = QUERY_CORE_ENTITY_MIN_HITRATE,
    require_primary_in_lead: bool = True,
    primary_lead_frac: float = PRIMARY_LEAD_FRAC,
) -> bool:
    """Decide whether ``text`` is irrelevant to ``query``.

    Three-stage check; any stage failing → irrelevant.

    Args:
        text: candidate document text.
        query: search query.
        min_hitrate: pass threshold for stage 1 (single-char density).
        core_entity_min_hitrate: pass threshold for stage 2 (entity coverage).
        require_primary_in_lead: enable stage 3 (topic-drift guard).
            Set ``False`` to opt out and restore two-stage behaviour.
        primary_lead_frac: fraction of text considered "lead" for stage 3.

    Returns:
        True if the document should be dropped (irrelevant).

    Examples:
        >>> is_query_irrelevant("黄金价格 上涨 投资分析", "黄金投资")
        False
        >>> is_query_irrelevant("Microsoft Visual Studio 18.3 release notes", "黄金投资")
        True
        >>> is_query_irrelevant("aliyun menu 存储 存储芯片 能效", "长鑫 DRAM")  # entity miss
        True
    """
    if not text or not query:
        return True

    # Stage 1: keyword density
    if query_keyword_density(text, query) < min_hitrate:
        return True

    # Stage 2: core-entity coverage
    hits, total = core_entity_hitrate(text, query)
    if total > 0 and (hits / total) < core_entity_min_hitrate:
        return True

    # Stage: primary entity in lead (topic-drift guard).
    # Rejects tangentially-related articles that *mention* the query
    # but aren't *about* it. Vacuously passes when query has no
    # primary entity (English-only queries, queries without a
    # ≥PRIMARY_MIN_LEN Chinese entity, etc.).
    if require_primary_in_lead:
        primary = _extract_primary_entity(query)
        if primary and not _primary_in_lead(text, primary, lead_frac=primary_lead_frac):
            return True

    return False


def explain_relevance(
    text: str,
    query: str,
    *,
    require_primary_in_lead: bool = True,
    primary_lead_frac: float = PRIMARY_LEAD_FRAC,
) -> RelevanceVerdict:
    """Diagnostic version of :func:`is_query_irrelevant` that returns the verdict.

    Returns:
        - :attr:`RelevanceVerdict.RELEVANT` if all three stages pass.
        - :attr:`RelevanceVerdict.IRRELEVANT_DENSITY` if stage 1 failed.
        - :attr:`RelevanceVerdict.IRRELEVANT_ENTITY` if stage 2 failed.
        - :attr:`RelevanceVerdict.IRRELEVANT_LEAD` if stage 3 failed.
    """
    if not text or not query:
        return RelevanceVerdict.IRRELEVANT_DENSITY
    if query_keyword_density(text, query) < QUERY_RELEVANCE_MIN_HITRATE:
        return RelevanceVerdict.IRRELEVANT_DENSITY
    hits, total = core_entity_hitrate(text, query)
    if total > 0 and (hits / total) < QUERY_CORE_ENTITY_MIN_HITRATE:
        return RelevanceVerdict.IRRELEVANT_ENTITY
    if require_primary_in_lead:
        primary = _extract_primary_entity(query)
        if primary and not _primary_in_lead(text, primary, lead_frac=primary_lead_frac):
            return RelevanceVerdict.IRRELEVANT_LEAD
    return RelevanceVerdict.RELEVANT


__all__ = [
    "query_keyword_density",
    "core_entity_hitrate",
    "_extract_core_entities",
    "is_query_irrelevant",
    "explain_relevance",
    "_extract_primary_entity",
    "_primary_in_lead",
    "PRIMARY_LEAD_FRAC",
    "PRIMARY_MIN_LEN",
]
