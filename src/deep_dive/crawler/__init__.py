"""Web crawler pipeline.

The crawler layer has three concerns:

1. **Search engines** (:mod:`deep_dive.crawler.engines`) — return a list of
   URLs in response to a query. Currently MMX and Tavily are implemented.
   Adding Brave / Google / Serper is a matter of writing one new module.

2. **Fetchers** (:mod:`deep_dive.crawler.fetchers`) — given a URL, return
   its raw HTML and title. Currently Playwright (Chromium headless) and
   cloudscraper (CF-bypass) are implemented, with the legacy fallback
   chain ``Playwright → cloudscraper``.

3. **Pipeline** (:mod:`deep_dive.crawler.pipeline`) — glues engines +
   fetchers + extraction + cookie loading + relevance checking into a
   single async coroutine that the orchestrator drives.

All modules are importable independently for unit testing.
"""

from __future__ import annotations

from .blacklist import is_baidu_domain, is_black_domain, is_lowq_domain, is_spam_domain
from .cookies import load_cookies, match_cookies_to_url
from .extraction import extract_main_text, looks_like_block_page

__all__ = [
    # Blacklist helpers
    "is_black_domain",
    "is_baidu_domain",
    "is_lowq_domain",
    "is_spam_domain",
    # Cookies
    "load_cookies",
    "match_cookies_to_url",
    # Extraction
    "extract_main_text",
    "looks_like_block_page",
]
