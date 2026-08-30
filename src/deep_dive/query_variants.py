"""Plan-driven query variants.

The LLM (host model) supplies a :class:`deep_dive.types.ResearchPlan` with all
variant queries pre-computed. This module converts a plan into the variant
dict used by the orchestrator's matrix builder.

API:
    generate_variants_from_plan(plan) -> dict[str, str]
    has_variant(variants, key) -> bool
    variant_note_label(key) -> str
"""

from __future__ import annotations

from typing import Any

from deep_dive.types import ResearchPlan


# Mapping of plan.variants keys → matrix task note labels. Used by
# build_search_matrix_from_plan() to label rows.
_VARIANT_NOTE_LABELS: dict[str, str] = {
    "refined": "中文细化",
    "critique": "中文评论/反对视角",
    "academic": "中文学术视角",
    "primary": "中文一手信源",
    "comparative": "中文对比视角",
}


def generate_variants_from_plan(plan: ResearchPlan) -> dict[str, str]:
    """Build the variants dict from a :class:`ResearchPlan`.

    Always includes:

        - ``"original"``: the raw user query
        - ``"en_query"``: first english_search_terms entry (or ``query`` if empty)

    Plus:

        - Plan-supplied variant queries (refined / critique / academic /
          primary / comparative) merged into the dict
        - English variants ``en_variant`` / ``en_academic`` if plan has
          multiple english_search_terms entries

    Args:
        plan: LLM-supplied research plan.

    Returns:
        Dict keyed by variant name. See ``deep_dive.constants.VARIANT_KEYS``.

    Examples:
        >>> p = ResearchPlan(query="x", english_search_terms=("X 2026", "X cases"))
        >>> v = generate_variants_from_plan(p)
        >>> v["original"]
        'x'
        >>> v["en_query"]
        'X 2026'
        >>> v["en_variant"]
        'X cases'
    """
    out: dict[str, str] = {"original": plan.query}

    # English baseline query — prefer first plan-supplied term; fall back
    # to the original query if no English terms were supplied (LLM decided
    # the query is zh-only).
    en_terms = list(plan.english_search_terms)
    if en_terms:
        out["en_query"] = en_terms[0]
    else:
        out["en_query"] = plan.query

    # Chinese variants from plan
    for key, query in plan.variants.items():
        if query and query.strip():
            out[key] = query.strip()

    # English variant / academic if multiple English terms supplied
    if len(en_terms) >= 2:
        out["en_variant"] = en_terms[1]
    if len(en_terms) >= 3:
        out["en_academic"] = en_terms[2]

    return out


def variant_note_label(variant_key: str) -> str:
    """Return the matrix task note label for a plan.variant key (Chinese variants).

    Used by ``build_search_matrix_from_plan()`` so report renderers see
    familiar labels (``"中文细化"``, ``"中文学术视角"``, ...) instead of
    raw variant keys.

    Returns the variant_key itself (untranslated) if no mapping exists.
    """
    return _VARIANT_NOTE_LABELS.get(variant_key, variant_key)


def has_variant(variants: dict[str, str], key: str) -> bool:
    """True if ``variants`` contains a non-empty entry for ``key``."""
    return key in variants and bool(variants[key].strip())


__all__ = ["generate_variants_from_plan", "variant_note_label", "has_variant"]