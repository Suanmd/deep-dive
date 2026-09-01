"""Plan-driven query classification.

The LLM (host model) supplies a :class:`ResearchPlan` whose ``kind`` field
names the query template; this module turns that into a QueryKind + target
sites + per-site template dict.

API:
    kind_from_plan(plan) -> QueryKind
    sites_from_plan(plan) -> list[str]
    template_from_plan(plan) -> dict[str, Any]
    pick_site_query(site, english_terms, *, fallback) -> str
    all_medium_alternatives() -> tuple[str, ...]
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from deep_dive.constants import LOWQ_DOMAINS, MEDIUM_ALTERNATIVES
from deep_dive.types import QueryKind, ResearchPlan

# Plan.kind strings accepted. Anything else → QueryKind.GENERAL.
_KIND_MAP: dict[str, QueryKind] = {
    "humanities": QueryKind.HUMANITIES,
    "tech": QueryKind.TECH,
    "academic": QueryKind.ACADEMIC,
    "news": QueryKind.NEWS,
    "business": QueryKind.BUSINESS,
    "movies": QueryKind.MOVIES,
    "general": QueryKind.GENERAL,
}


def kind_from_plan(plan: ResearchPlan) -> QueryKind:
    """Convert plan.kind string to QueryKind enum, defaulting to GENERAL.

    The plan's ``kind`` field is decided by the LLM — typically the
    host model classifies the query into one of the five templates
    (humanities / tech / academic / news / business) or ``"general"``.
    Unrecognised values fall through to GENERAL (no exception; safe for
    untrusted JSON input).

    Args:
        plan: LLM-supplied research plan.

    Returns:
        Matching :class:`QueryKind`, or :attr:`QueryKind.GENERAL`.
    """
    return _KIND_MAP.get(plan.kind, QueryKind.GENERAL)


def sites_from_plan(plan: ResearchPlan) -> list[str]:
    """Return ``plan.target_sites`` as a plain list (in priority order).

    The orchestrator slices this with ``[:n_site]`` where ``n_site``
    depends on depth (2 for normal, 3 for full, 0 for quick).
    """
    return list(plan.target_sites)


def template_from_plan(plan: ResearchPlan) -> dict[str, Any]:
    """Build a template dict compatible with the legacy ``template_for()`` API.

    Returns a dict shaped like::

        {
            "name": <kind.value>,
            "keywords": [],
            "exclude": <LOWQ_DOMAINS + spam domains>,
            "site_targets": <plan.target_sites>,
            "default_freshness": "",
        }

    Excludes are always populated from :data:`LOWQ_DOMAINS` since these
    are engineering blacklist entries (not LLM-supplied). Keywords are
    always empty in plan mode — the LLM doesn't classify by keyword
    presence; it just declares ``kind`` directly.
    """
    return {
        "name": kind_from_plan(plan).value,
        "keywords": [],
        "exclude": list(LOWQ_DOMAINS),
        "site_targets": list(plan.target_sites),
        "default_freshness": "",
    }


def all_medium_alternatives() -> tuple[str, ...]:
    """Return the Medium-alternative site list (compat shim for tests)."""
    return MEDIUM_ALTERNATIVES


# ---------------------------------------------------------------------------
# Per-site English term selection
# ---------------------------------------------------------------------------
#
# The site-targeted matrix builder used to plug ``english_search_terms[0]``
# (the English baseline) into every ``site:domain`` query. That works for
# sites whose name happens to appear in the baseline (lmsys.org ↔
# "… LMSYS"), but wastes the LLM-supplied ``english_search_terms[1:]``
# entries when target_sites have specialised vocabulary. Example from
# the LLM-leaderboard run:
#
#     target_sites        = ["lmsys.org", "huggingface.co", "arxiv.org", …]
#     english_search_terms = ["LLM leaderboard … LMSYS",
#                              "Open LLM Leaderboard Hugging Face 2026",
#                              "MMLU benchmark 2026 latest ranking", …]
#
# With the old logic, ``site:huggingface.co`` got the generic
# "LLM leaderboard … LMSYS" baseline — which retrieves 0 hits because
# HF is not in that term. With the new logic, ``huggingface.co`` gets
# ``english_search_terms[1]`` because substrings like "hugg"/"face"/
# "hugging" overlap with that term.
#
# Strategy: substring matching, NO hardcoded site alias table. The LLM
# is still the source of truth for which english terms to provide —
# this helper just routes them to the most relevant site.

# Tokens that should NEVER be considered when matching site domain to
# english_search_terms. Pure engineering constants (TLDs, common
# prefixes, subdomains) — not LLM-supplied decisions.
_SITE_TOKEN_STOPWORDS: frozenset[str] = frozenset(
    {
        "com",
        "co",
        "org",
        "net",
        "io",
        "ai",
        "cn",
        "uk",
        "jp",
        "de",
        "fr",
        "www",
        "blog",
        "docs",
        "wiki",
    }
)

# Subdomain prefixes stripped before substring generation. We don't
# try to enumerate every TLD — instead we let substrings containing
# "." be skipped (see loop body below). Stripping common subdomains
# up front keeps the candidate set small.
_SITE_PREFIX_RE = re.compile(r"^(?:www|docs|blog|m)\.", re.IGNORECASE)


def pick_site_query(
    site: str,
    english_search_terms: Iterable[str],
    *,
    fallback: str,
) -> str:
    """Pick the ``english_search_terms`` entry most likely to match ``site``.

    The previous site-targeted matrix builder used
    ``english_search_terms[0]`` for every site, which wasted
    ``terms[1:]`` when ``target_sites`` had specialised tokens
    (e.g. ``huggingface.co`` should match a term containing "Hugging
    Face", not the generic baseline).

    Algorithm (substring match, no hardcoded aliases):

        1. Lowercase + strip leading subdomain (www./docs./blog./m.).
        2. Generate all 4+ char alphanumeric substrings of the site.
           ``huggingface.co`` → {hugg, uggi, ggin, …, hugging, …,
           huggingface, …}. Substrings containing "." are skipped
           (avoids needing a full TLD list).
        3. Score each english term = number of substrings that appear
           in it. Ties → earliest index wins.
        4. If best score == 0 → return ``fallback``.

    Args:
        site: domain string like ``"lmsys.org"`` or
            ``"huggingface.co"``.
        english_search_terms: ordered iterable of english search queries
            (typically ``plan.english_search_terms``).
        fallback: query string to return when no term matches
            (caller passes ``english_search_terms[0]`` or
            ``plan.query``).

    Returns:
        The best-matching english term, or ``fallback`` if no term
        has any substring overlap with ``site``.

    Examples:
        >>> pick_site_query("lmsys.org",
        ...                  ["…LMSYS", "Hugging Face", "arxiv"], fallback="x")
        '…LMSYS'
        >>> pick_site_query("huggingface.co",
        ...                  ["…LMSYS", "Hugging Face 2026", "arxiv"], fallback="x")
        'Hugging Face 2026'
        >>> pick_site_query("zhihu.com",
        ...                  ["…LMSYS", "Hugging Face"], fallback="中文 知乎")
        '中文 知乎'
    """
    terms = list(english_search_terms or ())
    if not terms:
        return fallback

    site_lower = _SITE_PREFIX_RE.sub("", (site or "").lower().strip())

    # Generate every 4+ char alphanumeric substring as a match
    # candidate. This handles "huggingface.co" matching "Hugging Face"
    # via substrings like "hugg"/"face"/"hugging"/"huggingface"
    # without needing an alias table. Worst case O(L²) substrings
    # where L = len(site); in practice site ≤ 30 chars → ≤ 400 candidates.
    candidates: set[str] = set()
    for start in range(len(site_lower)):
        for end in range(start + 4, len(site_lower) + 1):
            sub = site_lower[start:end]
            if "." in sub:
                continue
            candidates.add(sub)

    # Drop pure-stopword candidates (rare — only matters for sites
    # like "co.org" where most substrings are stopwords).
    candidates = {c for c in candidates if c not in _SITE_TOKEN_STOPWORDS}
    if not candidates:
        return fallback

    best_idx = 0
    best_score = 0
    for i, term in enumerate(terms):
        term_lower = term.lower()
        score = sum(1 for c in candidates if c in term_lower)
        if score > best_score:
            best_score = score
            best_idx = i
    return terms[best_idx] if best_score > 0 else fallback


__all__ = [
    "kind_from_plan",
    "sites_from_plan",
    "template_from_plan",
    "pick_site_query",
    "all_medium_alternatives",
]
