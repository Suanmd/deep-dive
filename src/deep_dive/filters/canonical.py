"""URL canonicalization.

A canonical form is computed so the same logical URL always hashes to the
same fingerprint (used by the cookie matcher, the deduplicator, and the
report builder).

Pipeline:

    1. ``host`` → lowercase
    2. ``path`` → trailing slash stripped (unless ``/`` itself)
    3. ``query`` → tracking params stripped, remaining params sorted

The original behavior is preserved for compatibility — if you rely on
the exact output of the legacy function, see ``test_canonical.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from deep_dive.constants import TRACKING_PARAMS


def sort_query_params(query: str) -> str:
    """Sort ``a=1&b=2`` → ``a=1&b=2`` (already sorted) or ``b=2&a=1`` → ``a=1&b=2``.

    Args:
        query: the raw query string (without leading ``?``).

    Returns:
        A new query string with parameters sorted lexicographically by key.
    """
    if not query:
        return ""
    try:
        pairs = parse_qsl(query, keep_blank_values=True)
    except Exception:
        return query
    pairs.sort()
    return urlencode(pairs)


def strip_tracking(query: str, *, params: Iterable[str] | None = None) -> str:
    """Remove tracking parameters from a query string.

    Args:
        query: raw query string (without leading ``?``).
        params: parameter names to strip. Defaults to
            :data:`deep_dive.constants.TRACKING_PARAMS`.

    Returns:
        Query string with tracking params removed, parameters sorted.
    """
    if not query:
        return ""
    drop = set(params) if params is not None else TRACKING_PARAMS
    try:
        pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in drop]
    except Exception:
        return query
    pairs.sort()
    return urlencode(pairs)


def canonicalize_url(url: str) -> str:
    """Produce a stable canonical form of ``url``.

    Steps:
        1. Host → lowercase.
        2. Trailing slash on path stripped (unless path is ``/``).
        3. Query parameters: tracking removed, rest sorted.
        4. Fragment dropped.

    Args:
        url: any valid HTTP(S) URL.

    Returns:
        Canonical form. If the input cannot be parsed, it's returned
        unchanged (this matches legacy behavior — fail-soft, don't
        blow up a whole task over one weird URL).
    """
    try:
        p = urlparse(url)
    except Exception:
        return url

    if not p.scheme or not p.netloc:
        return url

    host = p.netloc.lower()
    path = p.path.rstrip("/") if p.path != "/" else "/"
    query = strip_tracking(p.query)

    # Drop fragment — most anti-tracking systems do this too.
    return urlunparse((p.scheme, host, path, p.params, query, ""))


__all__ = ["canonicalize_url", "strip_tracking", "sort_query_params"]
