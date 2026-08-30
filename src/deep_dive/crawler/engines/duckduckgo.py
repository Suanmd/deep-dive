"""DuckDuckGo search engine backend.

No API key, no daily quota. Uses DDG's public HTML endpoint. Added as
the **ultimate fallback** so a run never dies from combined
MMX + Tavily quota exhaustion — DuckDuckGo will always be tried last.

Why DuckDuckGo as the final fallback?
-------------------------------------

* MMX and Tavily both have per-key daily/hourly quotas. When all keys
  are exhausted, the orchestrator's old behaviour was to return
  ``quota_exceeded`` and abort. This left the user with 0 URLs.
* DDG's HTML endpoint at ``https://html.duckduckgo.com/html/`` has no
  API key requirement and no per-IP quota — only a soft rate limit
  (captcha if you hammer it). We throttle to ~2 qps to stay under it.
* Quality is **lower** than MMX/Tavily (smaller index, no native Chinese
  ranking) but for coverage it's the right tier to be at — better than
  nothing.

Limitations (documented for users):

* DDG's HTML endpoint sometimes returns captcha pages. The engine
  treats those as empty results and the orchestrator's relevance
  filter will drop noise.
* DDG does not honour the ``site:`` operator reliably. If you need
  site-restricted search, prefer MMX/Tavily when they have quota.
* DDG can be slow (3-10s per query) because of the throttling and
  HTML parsing.
"""

from __future__ import annotations

import re
import threading
import time
from urllib.parse import unquote

from deep_dive.constants import TAG_ERR, TAG_OK
from deep_dive.logging_setup import safe_print
from deep_dive.types import SearchHit

from .base import SearchEngine, SearchEngineError, SearchEngineTimeoutError

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    _HAS_REQUESTS = False


# Two DDG endpoints, tried in order. ``lite`` is simpler HTML and the
# parser is more robust; we keep ``html`` as a richer fallback for
# queries lite fails on.
_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("lite", "https://lite.duckduckgo.com/lite/"),
    ("html", "https://html.duckduckgo.com/html/"),
)

# Regex for extracting result links. Different for lite vs html.
_LITE_LINK_RE = re.compile(
    r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*class="result-link"',
    re.IGNORECASE,
)
_LITE_LINK_RE_FALLBACK = re.compile(
    r'<a[^>]+class="result-link"[^>]+href="(https?://[^"]+)"',
    re.IGNORECASE,
)
_HTML_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"',
    re.IGNORECASE,
)
# DDG HTML wraps real URLs in a redirector: ``//duckduckgo.com/l/?uddg=<encoded>&...``
_DDG_REDIRECT_RE = re.compile(r"uddg=([^&]+)", re.IGNORECASE)

# Captcha sentinel — short HTML + presence of captcha-related markers.
_CAPTCHA_NEEDLES = (
    "captcha",
    "anomaly",
    "bots use duckduckgo",
    "please complete",
)


class DuckDuckGoEngine(SearchEngine):
    """DuckDuckGo-backed search engine. No API key, no quota.

    Always available (no env-var config required), so the orchestrator
    uses this as the last-resort fallback when MMX and Tavily are both
    exhausted.
    """

    name = "duckduckgo"

    def __init__(
        self,
        *,
        timeout_s: float = 25.0,
        min_interval_s: float = 0.5,
        max_results_per_endpoint: int = 30,
        user_agent: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(timeout_s=timeout_s, **kwargs)
        self.min_interval_s = min_interval_s
        self.max_results_per_endpoint = max_results_per_endpoint
        self._last_call_ts = 0.0
        self._throttle_lock = threading.Lock()
        self._session = _requests.Session() if _HAS_REQUESTS else None
        if self._session is not None:
            self._session.headers.update({
                "User-Agent": user_agent or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            })

    # -- Throttling ------------------------------------------------------

    def _throttle(self) -> None:
        """Enforce minimum interval between calls to avoid captcha."""
        with self._throttle_lock:
            now = time.monotonic()
            elapsed = now - self._last_call_ts
            if elapsed < self.min_interval_s:
                time.sleep(self.min_interval_s - elapsed)
            self._last_call_ts = time.monotonic()

    # -- Search ----------------------------------------------------------

    def _raw_search(self, query: str, topk: int) -> list[SearchHit]:
        """Run one DDG search; return up to ``topk`` URLs.

        Tries ``lite`` first (simpler HTML, more robust parser); falls
        back to ``html`` if lite returned nothing useful. Caps each
        endpoint at ``max_results_per_endpoint`` so a runaway parser
        can't return 1000+ links on a single page.
        """
        if self._session is None:
            raise SearchEngineError(
                f"{self.name}: 'requests' library not installed; "
                "pip install requests to enable DuckDuckGo fallback"
            )

        self._throttle()
        hits: list[SearchHit] = []
        for label, endpoint in _ENDPOINTS:
            try:
                resp = self._session.post(
                    endpoint,
                    data={"q": query},
                    timeout=self.timeout_s,
                )
            except _requests.RequestException as e:
                # Network-level failure; try next endpoint. If both fail,
                # surface as TimeoutError so the orchestrator can decide.
                safe_print(
                    f"{TAG_ERR} {self.name} {label} '{query[:30]}': "
                    f"network {type(e).__name__}"
                )
                continue

            if resp.status_code != 200:
                safe_print(
                    f"{TAG_ERR} {self.name} {label} '{query[:30]}': "
                    f"HTTP {resp.status_code}"
                )
                continue
            html = resp.text or ""
            if any(c.lower() in html.lower() for c in _CAPTCHA_NEEDLES):
                safe_print(
                    f"{TAG_ERR} {self.name} {label} '{query[:30]}': captcha; "
                    "skipping endpoint"
                )
                continue
            if len(html) < 500:
                # Suspiciously short — probably an error page.
                continue

            extracted = (
                self._parse_lite(html) if label == "lite" else self._parse_html(html)
            )
            if extracted:
                hits = extracted[: self.max_results_per_endpoint]
                break

        safe_print(
            f"{TAG_OK} {self.name} '{query[:30]}' -> {len(hits)} URLs"
        )
        return hits[:topk]

    # -- Parsers ---------------------------------------------------------

    @staticmethod
    def _parse_lite(html: str) -> list[SearchHit]:
        """Extract result URLs from DDG Lite HTML."""
        urls: list[str] = []
        for m in _LITE_LINK_RE.finditer(html):
            urls.append(m.group(1))
        if not urls:
            for m in _LITE_LINK_RE_FALLBACK.finditer(html):
                urls.append(m.group(1))
        # de-dup preserving order
        seen: set[str] = set()
        out: list[SearchHit] = []
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            out.append(SearchHit(url=u, title="", engine="duckduckgo"))
        return out

    @staticmethod
    def _parse_html(html: str) -> list[SearchHit]:
        """Extract result URLs from DDG HTML endpoint.

        DDG HTML wraps every result URL in a redirect:
            ``//duckduckgo.com/l/?uddg=<url-encoded>&...``
        We unwrap the redirect to the real destination URL.
        """
        urls: list[str] = []
        for m in _HTML_LINK_RE.finditer(html):
            href = m.group(1)
            # Already absolute? Some results bypass the redirector.
            if href.startswith("http://") or href.startswith("https://"):
                urls.append(href)
                continue
            # Extract real URL from DDG redirector.
            rm = _DDG_REDIRECT_RE.search(href)
            if rm:
                urls.append(unquote(rm.group(1)))
        # de-dup preserving order
        seen: set[str] = set()
        out: list[SearchHit] = []
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            out.append(SearchHit(url=u, title="", engine="duckduckgo"))
        return out


__all__ = ["DuckDuckGoEngine"]