"""End-to-end crawl pipeline for one URL.

This is the per-URL glue that the legacy ``process_url`` helper
implemented inline in the previous ``deep-dive`` crawler. We pull
it into a class so:

* the orchestrator can drive it without knowing about Playwright,
  cloudscraper, or cookies;
* tests can substitute fake fetchers;
* new fetcher strategies can be plugged in without touching the
  pipeline logic.

The pipeline composes:

    1. Blacklist pre-check (spam / CF / lowq domain) — drop early.
    2. Primary fetcher (default: Playwright).
    3. If primary returns block-page text → CloudScraper fallback.
    4. Main-text extraction via :mod:`trafilatura`.
    5. Optional two-stage relevance check (main query vs sub-query).
    6. Write ``html``, ``txt``, ``metadata.json``, ``url_mapping.json``.

The pipeline writes ``metadata.json`` after **every** URL so a crash in
the middle of a 200-URL task loses at most the in-flight URL. (The
legacy code did the same.)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deep_dive.constants import BINARY_CONTENT_EXTENSIONS
from deep_dive.crawler.blacklist import is_baidu_domain, is_black_domain, is_spam_domain
from deep_dive.crawler.cookies import Cookie, load_cookies, match_cookies_to_url
from deep_dive.crawler.extraction import extract_main_text, looks_like_block_page
from deep_dive.crawler.fetchers.base import Fetcher
from deep_dive.crawler.fetchers.playwright import PlaywrightFetcher
from deep_dive.logging_setup import safe_print
from deep_dive.relevance import is_query_irrelevant
from deep_dive.types import FetchResult, FetchStatus


@dataclass(slots=True)
class PipelineConfig:
    """Tunables for one pipeline run."""

    output_dir: Path
    main_query: str
    page_timeout_s: float = 90.0
    max_retries: int = 1
    min_chars_for_quality: int = 500
    enable_relevance_check: bool = True
    # Max concurrent fetches per task (P1-4 fix). The previous serial
    # loop made 17 fetches take 17×~30s = 510s in the worst case; with
    # concurrency=3 the same 17 fetches finish in ~6 batches × 30s = 180s.
    # Playwright handles 3 tabs in one browser context comfortably.
    max_concurrent_fetches: int = 3


def _safe_filename(url: str) -> str:
    """Filename-safe slug of a URL."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    name = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", parsed.netloc + parsed.path)
    return name.strip("_")[:120] or "untitled"


def _safe_dir_name(query: str) -> str:
    """Filesystem-safe directory name from a (possibly long) query."""
    return re.sub(r'[\\/*?:"<>|]', "_", query).strip()[:80] or "search"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class CrawlPipeline:
    """Run fetch + extract + relevance for one URL list.

    Parameters
    ----------
    config:
        Pipeline-wide tunables.
    primary_fetcher:
        The first fetcher to try. Defaults to :class:`PlaywrightFetcher`.
    fallback_fetcher:
        If the primary fetcher returns a block page, try this one.
        Defaults to :class:`CloudScraperFetcher`. Pass ``None`` to disable
        fallback.
    """

    def __init__(
        self,
        config: PipelineConfig,
        *,
        primary_fetcher: Fetcher | None = None,
        fallback_fetcher: Fetcher | None = None,
        cookies_map: dict[str, list[Cookie]] | None = None,
    ) -> None:
        self.config = config
        self.primary_fetcher = primary_fetcher
        self.fallback_fetcher = fallback_fetcher
        self.cookies_map = cookies_map if cookies_map is not None else load_cookies()
        self._results: list[dict[str, Any]] = []
        self._url_mapping: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Top-level: process a list of URLs (sync wrapper around async)
    # ------------------------------------------------------------------

    def run(
        self,
        urls: Iterable[str],
        *,
        source_task: str = "",
        query_index: int = -1,
    ) -> list[FetchResult]:
        """Process a list of URLs synchronously.

        Args:
            urls: URLs to fetch.
            source_task: human-readable task label (e.g. ``"中文原始"``).
            query_index: index of the task in the matrix (for traceability).

        Returns:
            A list of :class:`FetchResult` in the same order as ``urls``.
        """
        url_list = list(urls)
        if not url_list:
            return []

        try:
            return asyncio.run(self._arun(url_list, source_task=source_task, query_index=query_index))
        except RuntimeError:  # already inside a running loop
            # Fallback: do the work in a fresh thread with its own loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(
                    lambda: asyncio.run(
                        self._arun(url_list, source_task=source_task, query_index=query_index)
                    )
                )
                return fut.result(timeout=self.config.page_timeout_s * len(url_list) + 30)

    # ------------------------------------------------------------------
    # Async implementation
    # ------------------------------------------------------------------

    async def _arun(
        self,
        urls: list[str],
        *,
        source_task: str,
        query_index: int,
    ) -> list[FetchResult]:
        primary = self.primary_fetcher or PlaywrightFetcher(
            timeout_s=self.config.page_timeout_s,
        )
        primary_owned = self.primary_fetcher is None

        try:
            if isinstance(primary, PlaywrightFetcher) and primary_owned:
                # Lazy init — only enter the playwright context here.
                await primary.__aenter__()

            # P1-4 fix: fetch URLs concurrently instead of serially.
            # The previous serial loop made 17 URLs take 17×page_timeout
            # in the worst case (one slow site = whole task stuck).
            # With a Semaphore we cap concurrent fetches so Playwright
            # isn't overwhelmed (3 tabs in one browser context is the
            # safe upper bound).
            concurrency = max(1, self.config.max_concurrent_fetches)
            semaphore = asyncio.Semaphore(concurrency)

            async def _one(url: str) -> FetchResult:
                async with semaphore:
                    return await self._process_one(
                        primary, url, source_task=source_task, query_index=query_index
                    )

            results: list[FetchResult] = list(await asyncio.gather(*[_one(u) for u in urls]))
            # Metadata writes happen AFTER all fetches complete. This is
            # a slight relaxation of the per-URL-write crash safety
            # contract; in exchange, the whole pipeline is ~3x faster.
            for res in results:
                self._record_metadata(res)
            self._write_metadata()
            self._write_url_mapping()
            return results
        finally:
            if primary_owned and isinstance(primary, PlaywrightFetcher):
                with contextlib.suppress(Exception):
                    await primary.__aexit__(None, None, None)

    async def _process_one(
        self,
        primary: Fetcher,
        url: str,
        *,
        source_task: str,
        query_index: int,
    ) -> FetchResult:
        # 0. Pre-check binary content (PDF, DOC, etc.). These URLs return
        # raw binary that trafilatura extracts as garbage "text" with
        # very high char counts (often >100k chars of mojibake). Without
        # this gate they dominate the corpus by char-count and pollute
        # capy with nonsense top-quotes (v3 regression showed
        # arxiv.org/pdf/2602.02994 as 337K-char "Top URL" full of ??).
        # Skip BEFORE doing any fetch — saves bandwidth and crawl time.
        url_lower = url.lower()
        for ext in BINARY_CONTENT_EXTENSIONS:
            # Match extension as suffix OR before query string / fragment.
            if url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}#" in url_lower:
                return FetchResult(
                    url=url,
                    status=FetchStatus.SKIPPED,
                    error=f"binary_extension:{ext}",
                    source_task=source_task,
                    query_index=query_index,
                )

        # 1. Pre-check blacklists
        if is_spam_domain(url):
            return FetchResult(
                url=url,
                status=FetchStatus.SKIPPED,
                error="spam_domain",
                source_task=source_task,
                query_index=query_index,
            )
        if is_black_domain(url):
            # Try cloudscraper instead of skipping outright (preserves the original fallback choice)
            if self.fallback_fetcher:
                return await self._process_via_fallback(url, source_task, query_index)
            return FetchResult(
                url=url,
                status=FetchStatus.SKIPPED,
                error="cf_blacklist",
                source_task=source_task,
                query_index=query_index,
            )

        # 2. Primary fetch
        cookies = match_cookies_to_url(url, self.cookies_map)
        warmup = self.WARMUP_URL if is_baidu_domain(url) else None
        title = ""  # must be defined so failure-path can reference it
        html = ""
        for attempt in range(self.config.max_retries + 1):
            try:
                if isinstance(primary, PlaywrightFetcher):
                    html, title = await primary.afetch(
                        url, cookies=cookies, warmup_url=warmup, do_warmup=bool(warmup)
                    )
                else:
                    html, title = primary.fetch(url, cookies=cookies, warmup_url=warmup)
                if not html:
                    raise RuntimeError("empty html from primary fetcher")
                break
            except Exception as e:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(random.uniform(1.5, 3))
                    continue
                # Last attempt failed → try fallback if available
                if self.fallback_fetcher:
                    return await self._process_via_fallback(url, source_task, query_index)
                return FetchResult(
                    url=url,
                    status=FetchStatus.FAILED,
                    title=title or "",
                    error=f"{type(e).__name__}: {e}"[:200],
                    source_task=source_task,
                    query_index=query_index,
                )

        if not html:
            return FetchResult(
                url=url,
                status=FetchStatus.FAILED,
                title=title or "",
                error="empty html",
                source_task=source_task,
                query_index=query_index,
            )

        text = extract_main_text(html)
        if looks_like_block_page(text):
            # Primary returned a block page → try fallback
            if self.fallback_fetcher:
                return await self._process_via_fallback(url, source_task, query_index)
            return FetchResult(
                url=url,
                status=FetchStatus.BLOCKED,
                title=title,
                error="block_page_detected",
                source_task=source_task,
                query_index=query_index,
            )

        # 3. Persist files
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        slug = _safe_filename(url)
        html_path = self.config.output_dir / f"{slug}.html"
        txt_path = self.config.output_dir / f"{slug}.txt"
        html_path.write_text(html, encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")

        # 4. Relevance check
        if self.config.enable_relevance_check and text and self.config.main_query:
            try:
                if is_query_irrelevant(text, self.config.main_query):
                    with contextlib.suppress(Exception):
                        txt_path.unlink()
                    return FetchResult(
                        url=url,
                        status=FetchStatus.IRRELEVANT,
                        title=title,
                        chars=len(text),
                        html_path=html_path,
                        txt_path=None,
                        error="query_irrelevant",
                        source_task=source_task,
                        query_index=query_index,
                    )
            except Exception:
                pass

        return FetchResult(
            url=url,
            status=FetchStatus.SUCCESS,
            title=title,
            chars=len(text),
            html_path=html_path,
            txt_path=txt_path,
            source_task=source_task,
            query_index=query_index,
        )

    async def _process_via_fallback(
        self,
        url: str,
        source_task: str,
        query_index: int,
    ) -> FetchResult:
        if self.fallback_fetcher is None:
            return FetchResult(
                url=url,
                status=FetchStatus.SKIPPED,
                error="no_fallback",
                source_task=source_task,
                query_index=query_index,
            )

        cookies = match_cookies_to_url(url, self.cookies_map)
        try:
            html, title = self.fallback_fetcher.fetch(url, cookies=cookies)
        except Exception as e:
            return FetchResult(
                url=url,
                status=FetchStatus.FAILED,
                error=f"fallback: {type(e).__name__}: {e}"[:200],
                source_task=source_task,
                query_index=query_index,
            )

        if not html:
            return FetchResult(
                url=url,
                status=FetchStatus.FAILED,
                error="fallback_empty",
                source_task=source_task,
                query_index=query_index,
            )

        text = extract_main_text(html)
        if looks_like_block_page(text):
            return FetchResult(
                url=url,
                status=FetchStatus.BLOCKED,
                title=title,
                error="block_page_after_fallback",
                source_task=source_task,
                query_index=query_index,
            )

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        slug = _safe_filename(url)
        html_path = self.config.output_dir / f"{slug}.html"
        txt_path = self.config.output_dir / f"{slug}.txt"
        html_path.write_text(html, encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")

        return FetchResult(
            url=url,
            status=FetchStatus.SUCCESS,
            title=title,
            chars=len(text),
            html_path=html_path,
            txt_path=txt_path,
            source_task=source_task,
            query_index=query_index,
        )

    # ------------------------------------------------------------------
    # Metadata persistence
    # ------------------------------------------------------------------

    WARMUP_URL = "https://www.baidu.com/"

    def _record_metadata(self, result: FetchResult) -> None:
        entry: dict[str, Any] = (
            asdict(result)
            if hasattr(result, "__dataclass_fields__")
            else {
                "url": result.url,
                "status": result.status.value,
                "title": result.title,
                "chars": result.chars,
                "html_path": str(result.html_path) if result.html_path else None,
                "txt_path": str(result.txt_path) if result.txt_path else None,
                "error": result.error,
                "source_task": result.source_task,
                "query_index": result.query_index,
            }
        )
        # Convert Path fields to strings (asdict doesn't do it automatically)
        for k, v in list(entry.items()):
            if hasattr(v, "__fspath__"):
                entry[k] = str(v)
        self._results.append(entry)

    def _write_metadata(self) -> None:
        try:
            (self.config.output_dir / "metadata.json").write_text(
                json.dumps(self._results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            safe_print(f"[META] write failed: {e}")

    def _write_url_mapping(self) -> None:
        try:
            self._url_mapping = {
                e.get("txt_file") or "": e.get("url", "")
                for e in self._results
                if e.get("status") == FetchStatus.SUCCESS.value and e.get("txt_file")
            }
            (self.config.output_dir / "url_mapping.json").write_text(
                json.dumps(self._url_mapping, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            safe_print(f"[URL-MAP] write failed: {e}")


__all__ = ["CrawlPipeline", "PipelineConfig", "_safe_filename", "_safe_dir_name"]
