"""URL fetchers.

A fetcher turns a URL into ``(html, title)`` (or fails). They don't
extract main text (that's :mod:`deep_dive.crawler.extraction`) and they
don't run relevance checks (that's :mod:`deep_dive.relevance`).

Currently implemented:

* :class:`PlaywrightFetcher` — Chromium headless via Playwright. The
  default; handles JS-rendered SPAs.
* :class:`CloudScraperFetcher` — synchronous, no JS execution, but
  bypasses Cloudflare. Used as fallback for sites that block headless
  Chromium.

Adding a new fetcher (e.g. ``httpx`` for trivial GETs) is a matter of
subclassing :class:`Fetcher`.
"""

from __future__ import annotations

from .base import Fetcher, FetcherError
from .cloudscraper import CloudScraperFetcher
from .playwright import PlaywrightFetcher

__all__ = [
    "Fetcher",
    "FetcherError",
    "CloudScraperFetcher",
    "PlaywrightFetcher",
]
