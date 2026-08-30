"""Main-text extraction from raw HTML.

Wraps :mod:`trafilatura` with a project-friendly API.

Why not use ``readability-lxml``?
--------------------------------

The legacy code compared trafilatura favorably (less boilerplate, fewer
false positives on Chinese article pages). We keep that choice for
behavioral compatibility.

Why wrap at all?
----------------

1. **Fail-soft** — trafilatura raises on weird HTML; we want an empty
   string, not a crash, for one bad page in a batch of 200.
2. **Single import point** — easy to swap if we ever want a different
   extractor (e.g. ``justext``, ``boilerpy3``).
3. **Consistent options** — projects tend to drift in their extraction
   options (with/without tables, comments, etc.). Centralizing here
   means one config knob affects every caller.
"""

from __future__ import annotations

import re
from typing import Final

from deep_dive.constants import BLOCK_KEYWORDS

try:
    import trafilatura
    _HAS_TRAFILATURA = True
except ImportError:  # pragma: no cover — trafilatura is a hard dep
    _HAS_TRAFILATURA = False


_DEFAULT_OPTS: Final[dict[str, bool]] = {
    "include_comments": False,
    "include_tables": False,
    "favor_recall": False,
    "with_metadata": False,
}

_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def extract_main_text(html: str) -> str:
    """Return the main article text of ``html``, or ``""`` on failure.

    Args:
        html: full HTML string (any encoding — trafilatura decodes).

    Returns:
        Cleaned text. Empty string if extraction returns nothing
        useful or if trafilatura isn't installed.
    """
    if not html:
        return ""
    if not _HAS_TRAFILATURA:
        return ""
    try:
        text = trafilatura.extract(html, **_DEFAULT_OPTS)
    except Exception:
        return ""
    return (text or "").strip()


def extract_title(html: str, *, max_length: int = 200) -> str:
    """Pull ``<title>`` out of ``html`` and normalize whitespace.

    Args:
        html: HTML string.
        max_length: clip result to this many characters (default 200).

    Returns:
        Title text with runs of whitespace collapsed. Empty string if
        no ``<title>`` element is found.
    """
    if not html:
        return ""
    m = _TITLE_RE.search(html)
    if not m:
        return ""
    title = _WS_RE.sub(" ", m.group(1)).strip()
    return title[:max_length]


def looks_like_block_page(text: str) -> bool:
    """Heuristic check for CAPTCHA / WAF / "please log in" pages.

    Mirrors the legacy ``is_block_page(text)`` function. Returns True
    when ``text`` is empty, near-empty, or contains one of the
    well-known block-page phrases.

    Args:
        text: extracted plain-text content.

    Returns:
        True if the page should be considered blocked and excluded
        from the report.
    """
    if not text or len(text.strip()) < 20:
        return True
    low = text.lower()
    return any(kw.lower() in low for kw in BLOCK_KEYWORDS)


__all__ = ["extract_main_text", "extract_title", "looks_like_block_page"]
