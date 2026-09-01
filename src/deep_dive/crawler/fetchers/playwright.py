"""Playwright-based fetcher.

Implements the legacy ``fetch_page`` behaviour (used by the previous
deep-dive crawler) inside its own class so it can be tested in
isolation and swapped out for alternative implementations.

Notes
-----

* Uses ``async_playwright`` and runs everything inside one shared
  browser context (the legacy code created a new browser per process;
  we leave that to the caller for compatibility).
* Has the same anti-detection tweaks (``navigator.webdriver`` undef,
  plugins spoofing, language).
* Random sleep + click to mimic humans.
* Special-case for Baidu properties: warm-up visit to ``baidu.com`` and
  simulated search-box click to bypass first-visit challenges.

The class does **not** import or depend on ``trafilatura``; main-text
extraction is the pipeline's responsibility.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Iterable
from typing import Any, cast

from deep_dive.constants import USER_AGENTS, VIEWPORTS
from deep_dive.logging_setup import safe_print

from .base import Fetcher, FetcherError

try:
    from playwright.async_api import Browser, Playwright, async_playwright

    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover — playwright is a hard dep
    _HAS_PLAYWRIGHT = False


_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' });
window.navigator.chrome = { runtime: {} };
"""


class PlaywrightFetcher(Fetcher):
    """Async Playwright-based fetcher.

    Typical usage::

        async with PlaywrightFetcher() as fetcher:
            html, title = await fetcher.afetch(url, cookies=...)
    """

    name = "playwright"
    WARMUP_URL = "https://www.baidu.com/"

    def __init__(
        self,
        *,
        timeout_s: float = 90.0,
        headless: bool = True,
        warmup_for_baidu: bool = True,
        extra_browser_args: Iterable[str] | None = None,
    ) -> None:
        super().__init__(timeout_s=timeout_s)
        self.headless = headless
        self.warmup_for_baidu = warmup_for_baidu
        self.extra_browser_args = list(
            extra_browser_args
            or [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-popup-blocking",
            ]
        )
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> PlaywrightFetcher:
        if not _HAS_PLAYWRIGHT:
            raise FetcherError("playwright not installed")
        pw = await async_playwright().start()
        self._pw = pw
        self._browser = await pw.chromium.launch(
            headless=self.headless,
            args=self.extra_browser_args,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

    # -- Public async fetch --------------------------------------------

    async def afetch(
        self,
        url: str,
        *,
        cookies: list[dict[str, str]] | None = None,
        warmup_url: str | None = None,
        do_warmup: bool = False,
    ) -> tuple[str, str]:
        """Async Playwright fetch with warm-up + cookie + lazy-scroll.

        Args:
            url: target URL.
            cookies: optional cookies to inject.
            warmup_url: optional explicit warm-up URL (overrides Baidu default).
            do_warmup: if True and ``warmup_for_baidu`` is set, visit the
                Baidu warm-up URL first to dodge first-visit CAPTCHAs.

        Returns:
            ``(html, title)`` tuple.
        """
        if self._browser is None:
            raise FetcherError("PlaywrightFetcher must be used as an async context manager")

        page = await self._browser.new_page(
            user_agent=random.choice(USER_AGENTS),
            viewport=cast("Any", random.choice(VIEWPORTS)),
            ignore_https_errors=True,
        )
        try:
            await page.add_init_script(_INIT_SCRIPT)

            # Warm-up: visit a benign URL first to seed cookies.
            warm = warmup_url
            if warm is None and do_warmup and self.warmup_for_baidu:
                warm = self.WARMUP_URL
            if warm:
                try:
                    await page.goto(warm, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    await page.evaluate("document.querySelector('input#kw')?.focus();")
                    await asyncio.sleep(random.uniform(0.4, 0.8))
                except Exception:
                    pass  # warm-up failures never abort the main fetch

            if cookies:
                try:
                    await page.context.add_cookies(cast("Any", cookies))
                except Exception as e:
                    safe_print(f"[COOKIE-WARN] inject failed: {e}")

            await asyncio.sleep(random.uniform(0.2, 0.6))
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.timeout_s * 1000))
            await asyncio.sleep(random.uniform(0.8, 1.8))

            # Scroll to trigger lazy loads
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3);")
                await asyncio.sleep(0.4)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await asyncio.sleep(0.4)
            except Exception:
                pass

            # Random click — anti-fingerprint
            with contextlib.suppress(Exception):
                await page.mouse.click(random.randint(150, 600), random.randint(150, 600))

            # ``page.content()`` can race against navigation; retry up to 3x.
            content: str | None = None
            title: str = ""
            for _ in range(3):
                try:
                    content = await page.content()
                    title = await page.title()
                    break
                except Exception:
                    await asyncio.sleep(0.8)

            return content or "", (title or "").strip()
        finally:
            with contextlib.suppress(Exception):
                await page.close()

    # Sync fallback delegates to async via a fresh event loop.
    def fetch(
        self,
        url: str,
        *,
        cookies: list[dict[str, str]] | None = None,
        warmup_url: str | None = None,
    ) -> tuple[str, str]:
        """Sync wrapper — spins up a fresh event loop and delegates to
        :meth:`afetch`. Use ``async with PlaywrightFetcher()`` directly
        in async code instead (avoids the loop-spinup overhead).
        """

        async def _run():
            async with PlaywrightFetcher(
                timeout_s=self.timeout_s,
                headless=self.headless,
                warmup_for_baidu=self.warmup_for_baidu,
                extra_browser_args=self.extra_browser_args,
            ) as f:
                return await f.afetch(url, cookies=cookies, warmup_url=warmup_url)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(_run())
        finally:
            with contextlib.suppress(Exception):
                loop.close()


__all__ = ["PlaywrightFetcher"]
