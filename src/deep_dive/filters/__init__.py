"""URL filtering utilities.

Two related but independent primitives:

* :mod:`deep_dive.filters.canonical` — URL canonicalization (host case,
  trailing slash, query-param ordering, tracking-param stripping).
* :mod:`deep_dive.filters.url_filter` — full-dimension filtering
  (canonical → spam/blacklist domain → path pattern → lowq host → exact
  dedup → per-domain cap).

Both modules are pure-Python with no I/O, so they're trivially testable.
"""

from __future__ import annotations

from .canonical import canonicalize_url, strip_tracking, sort_query_params
from .url_filter import (
    FilterStats,
    smart_filter_urls,
)

__all__ = [
    "canonicalize_url",
    "strip_tracking",
    "sort_query_params",
    "smart_filter_urls",
    "FilterStats",
]
