"""Cloudscraper-based fetcher.

Synchronous HTTP fetcher with built-in Cloudflare bypass. Used as a
fallback for sites that detect and block Playwright (e.g. ``goodreads``,
``99csw``, ``book118``, ``weread``).

Limitations vs. Playwright:

* No JS execution. Sites that need JS to render content will return
  mostly-empty pages.
* No screenshot or interaction.

The implementation matches the legacy ``cloudscraper_fetch`` function
behaviour-for-behaviour, just wrapped in a :class:`Fetcher` subclass.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable

from deep_dive.constants import USER_AGENTS
from deep_dive.logging_setup import safe_print
from deep_dive.crawler.encoding import smart_decode_bytes

from .base import Fetcher, FetcherError

try:
    import cloudscraper
    _HAS_CLOUDSCRAPER = True
except ImportError:  # pragma: no cover — cloudscraper is a hard dep
    _HAS_CLOUDSCRAPER = False


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


class CloudScraperFetcher(Fetcher):
    """Synchronous CF-bypass fetcher via the cloudscraper library."""

    name = "cloudscraper"

    def __init__(
        self,
        *,
        timeout_s: float = 60.0,
        browser: str = "chrome",
        platform: str = "windows",
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self.browser = browser
        self.platform = platform

    def _make_scraper(self):
        if not _HAS_CLOUDSCRAPER:
            raise FetcherError("cloudscraper not installed")
        return cloudscraper.create_scraper(
            browser={"browser": self.browser, "platform": self.platform, "mobile": False}
        )

    def fetch(
        self,
        url: str,
        *,
        cookies: list[dict[str, str]] | None = None,
        warmup_url: str | None = None,  # unused — cloudscraper has no browser context
    ) -> tuple[str, str]:
        try:
            scraper = self._make_scraper()
        except FetcherError:
            raise
        except Exception as e:
            raise FetcherError(str(e)) from e

        if cookies:
            for c in cookies:
                try:
                    scraper.cookies.set(c["name"], c["value"], domain=c.get("domain"))
                except Exception:
                    pass

        scraper.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        try:
            resp = scraper.get(url, timeout=self.timeout_s)
            resp.raise_for_status()
        except Exception as e:
            raise FetcherError(f"cloudscraper GET failed: {e}") from e

        # Decode response bytes with explicit fallback chain instead of
        # ``resp.text`` (which falls back to ISO-8859-1 per HTTP/1.1
        # §3.7.1 when charset is undeclared — the source of the
        # double-mojibake seen on Chinese sites like nfnews.com).
        # Prefer ``apparent_encoding`` (charset_normalizer-detected),
        # fall back to ``resp.encoding`` (Content-Type charset or None).
        hint = getattr(resp, "apparent_encoding", None) or resp.encoding
        html = smart_decode_bytes(resp.content or b"", hint=hint)
        m = _TITLE_RE.search(html)
        title = _WS_RE.sub(" ", m.group(1)).strip() if m else ""
        return html, title[:200]


__all__ = ["CloudScraperFetcher"]
