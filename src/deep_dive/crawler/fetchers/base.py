"""Abstract base for URL fetchers."""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass


class FetcherError(Exception):
    """Generic fetcher failure."""


@dataclass(slots=True, frozen=True)
class CookieHint:
    """Subset of a cookie in Playwright shape, passed through the fetcher.

    The fetcher doesn't load cookies itself; the pipeline injects them
    via :class:`CookieHint` so the fetcher stays dependency-free of
    the cookie loader.
    """

    name: str
    value: str
    domain: str
    path: str = "/"


class Fetcher(abc.ABC):
    """Base class for URL fetchers.

    Subclasses implement :meth:`fetch` (sync) or :meth:`afetch` (async).
    The pipeline calls :meth:`afetch` if available, falling back to a
    threadpool-wrapped :meth:`fetch`.
    """

    name: str = "abstract"

    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s

    # -- Sync API -------------------------------------------------------

    @abc.abstractmethod
    def fetch(
        self,
        url: str,
        *,
        cookies: list[dict[str, str]] | None = None,
        warmup_url: str | None = None,
    ) -> tuple[str, str]:
        """Fetch ``url`` and return ``(html, title)``.

        Args:
            url: target URL.
            cookies: optional list of cookie dicts to inject
                (Playwright-compatible shape).
            warmup_url: optional "warm-up" URL to visit first (used
                for the Baidu bypass that pre-loads baidu.com to
                dodge first-visit CAPTCHAs).

        Returns:
            Tuple ``(html, title)``. Both may be empty on failure.

        Raises:
            FetcherError: for transport-level failures (timeout,
                SSL, connection reset).
        """

    # -- Async API ------------------------------------------------------

    async def afetch(
        self,
        url: str,
        *,
        cookies: list[dict[str, str]] | None = None,
        warmup_url: str | None = None,
    ) -> tuple[str, str]:
        """Async default: run sync fetch in executor."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.fetch(url, cookies=cookies, warmup_url=warmup_url),
        )


__all__ = ["Fetcher", "FetcherError", "CookieHint"]
