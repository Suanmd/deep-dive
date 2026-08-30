"""Full-dimension URL filter (legacy ``smart_filter_urls``).

This was the v4.4 flagship feature: a single, deterministic function
that you can call from anywhere in the pipeline to drop spam URLs,
tracking parameters, low-quality hosts, etc.

Pipeline
--------

    1. Blacklist domains (spam + CF + lowq).
    2. Blacklist path patterns (/login, /signup, /cart, ?sort=, ...).
    3. Low-quality host patterns (kongfz item pages, weread paywall,
       book118 template pages, ...).
    4. Canonicalize + tracking strip.
    5. Exact dedup on canonical form.
    6. Per-domain cap (optional).

Each step counts its drops; the final :class:`FilterStats` lets callers
diagnose why so many URLs got cut (handy when a Tavily quota warning is
actually a "everything we got was spam" problem).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from deep_dive.constants import (
    _BLACKLIST_PATH_PATTERNS,
    _LOWQ_HOST_PATTERNS,
    CF_BLACK_DOMAINS,
    LOWQ_DOMAINS,
    SPAM_DOMAINS,
)
from deep_dive.logging_setup import safe_print

from .canonical import canonicalize_url


@dataclass(slots=True)
class FilterStats:
    """Why-drops counter returned by :func:`smart_filter_urls`.

    Useful for diagnostic logging. The legacy code printed a one-line
    summary like::

        [SMART-FILTER] kept=18/30 | dropped: {'spam_domain': 4, 'blacklist_path': 2, ...}

    This struct is the structured form of that.
    """

    total_in: int = 0
    total_kept: int = 0
    spam_domain: int = 0
    blacklist_path: int = 0
    lowq_host: int = 0
    dup: int = 0
    per_domain_cap: int = 0
    invalid: int = 0

    def as_log_line(self) -> str:
        return (
            f"kept={self.total_kept}/{self.total_in} | "
            f"dropped: {self.to_drop_dict()}"
        )

    def to_drop_dict(self) -> dict[str, int]:
        return {
            "spam_domain": self.spam_domain,
            "blacklist_path": self.blacklist_path,
            "lowq_host": self.lowq_host,
            "dup": self.dup,
            "per_domain_cap": self.per_domain_cap,
            "invalid": self.invalid,
        }


def _is_spam_or_black(url: str) -> bool:
    """True if URL host matches any of the SPAM / CF / LOWQ domain lists."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return True  # unparseable → treat as spam (drop)
    if not host:
        return True
    for d in SPAM_DOMAINS:
        if d in host:
            return True
    for d in CF_BLACK_DOMAINS:
        if d in host:
            return True
    for d in LOWQ_DOMAINS:
        if d in host:
            return True
    return False


def _has_blacklist_path(url: str) -> bool:
    return any(p.search(url) for p in _BLACKLIST_PATH_PATTERNS)


def _has_lowq_host(url: str) -> bool:
    return any(p.search(url) for p in _LOWQ_HOST_PATTERNS)


def smart_filter_urls(
    urls: Iterable[str] | None,
    *,
    keep_per_domain: int | None = None,
    verbose: bool = False,
) -> list[str]:
    """Filter ``urls`` through the full pipeline.

    Args:
        urls: input URL iterable (strings). ``None`` is treated as empty.
        keep_per_domain: if set, cap each domain at this many URLs (post-canonical).
            ``None`` (default) means no cap.
        verbose: print a one-line diagnostic summary to stderr.

    Returns:
        A new list of URLs (in their original input order, post-canonical).
    """
    stats = FilterStats()
    if urls is None:
        if verbose:
            safe_print(f"[SMART-FILTER] kept=0/0")
        return []

    out: list[str] = []
    domain_count: dict[str, int] = {} if keep_per_domain else {}

    for url in urls:
        stats.total_in += 1
        if not url or not isinstance(url, str):
            stats.invalid += 1
            continue

        # 1. Domain blacklist
        if _is_spam_or_black(url):
            stats.spam_domain += 1
            continue

        # 2. Path-pattern blacklist
        if _has_blacklist_path(url):
            stats.blacklist_path += 1
            continue

        # 3. Low-quality host pattern
        if _has_lowq_host(url):
            stats.lowq_host += 1
            continue

        # 4. Canonicalize (host lowercase, trailing slash, tracking strip,
        #    query sort) and dedup against already-emitted
        canon = canonicalize_url(url)
        if canon in out:
            stats.dup += 1
            continue

        # 5. Per-domain cap (post-canonical)
        if keep_per_domain:
            try:
                dom = urlparse(canon).netloc
            except Exception:
                dom = ""
            if dom:
                cnt = domain_count.get(dom, 0)
                if cnt >= keep_per_domain:
                    stats.per_domain_cap += 1
                    continue
                domain_count[dom] = cnt + 1

        out.append(canon)

    stats.total_kept = len(out)

    if verbose:
        safe_print(f"[SMART-FILTER] {stats.as_log_line()}")

    return out


__all__ = ["FilterStats", "smart_filter_urls"]
