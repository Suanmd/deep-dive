"""Abstract base for search engines.

A search engine takes a query and returns a list of URLs. It does **not**
fetch the URLs — that's the fetcher's job. Keeping the two concerns
separate means we can swap engines without touching the fetcher chain
(and vice versa).

Multi-key design
---------------

Engines that have per-credential state (Tavily API keys, multiple MMX
binaries/profiles) should subclass :class:`MultiKeyEngine` and implement
:meth._try_with_credential. The base class handles sequential rotation:

* If the current credential fails with a :class:`SearchEngineQuotaError`,
  :class:`SearchEngineAuthError`, or :class:`SearchEngineNetworkError`,
  mark it exhausted and try the next credential.
* Once all credentials are exhausted, surface
  :class:`SearchEngineQuotaError` so the orchestrator can escalate.
* Per-call audit (``key_used``, ``keys_tried``, ``keys_exhausted``) is
  stored in thread-local state so concurrent tasks sharing one engine
  instance don't clobber each other.
"""

from __future__ import annotations

import abc
import asyncio
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutTimeoutError
from dataclasses import dataclass
from typing import Any

from deep_dive.filters.url_filter import smart_filter_urls
from deep_dive.types import SearchHit


class SearchEngineError(Exception):
    """Generic engine failure (network, auth, etc.)."""


class SearchEngineQuotaError(SearchEngineError):
    """Raised when the engine reports quota / rate-limit exhaustion."""


class SearchEngineTimeoutError(SearchEngineError):
    """Raised when the engine exceeds its budget without returning."""


class SearchEngineAuthError(SearchEngineError):
    """Raised when the engine reports auth failure (401/403/invalid key).

    Treated as **retryable across credentials** by :class:`MultiKeyEngine` —
    the current key is bad, but the next one might work.
    """


class SearchEngineNetworkError(SearchEngineError):
    """Raised when the engine reports a network-level failure (SSL/DNS/conn).

    Treated as **retryable across credentials** by :class:`MultiKeyEngine` —
    different keys may use different network paths / regions.
    """


# ---------------------------------------------------------------------------
# Multi-credential primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineCredential:
    """One credential for a search engine.

    Different engines care about different fields:

    * **Tavily**: only ``key`` (the API key string).
    * **MMX** (CLI-based): ``path`` (binary override), ``env``
      (env-var overrides such as ``MMX_API_KEY``), ``args`` (extra CLI args).
    * Future engines: any combination of the above.

    Attributes:
        name: human-readable identifier (e.g., ``"KEY1"``, ``"personal"``).
              Used in audit logs / TaskResult.extra.
        key: API key string (Tavily).
        env: env-var overrides for this credential.
        args: extra CLI args for this credential.
        path: executable path override.
    """

    name: str
    key: str | None = None
    env: Mapping[str, str] | None = None
    args: tuple[str, ...] = ()
    path: str | None = None


class EngineAccountPool:
    """A pool of credentials for one engine.

    Tracks which credentials have been marked as **exhausted** during this
    run. Use :meth:`reset` to clear state for a fresh run.

    Operations are thread-safe via an internal lock so concurrent tasks
    sharing one engine instance don't race on the ``exhausted`` set.
    """

    def __init__(
        self, credentials: list[EngineCredential] | tuple[EngineCredential, ...], *, name: str = "pool"
    ) -> None:
        self.name = name
        self.credentials: tuple[EngineCredential, ...] = tuple(credentials)
        self.exhausted: set[str] = set()
        self._lock = threading.Lock()

    def next_active(self) -> EngineCredential | None:
        """Return the next non-exhausted credential, or ``None``."""
        with self._lock:
            for c in self.credentials:
                if c.name not in self.exhausted:
                    return c
        return None

    def mark_exhausted(self, name: str) -> None:
        """Mark a credential as exhausted for this run (thread-safe)."""
        with self._lock:
            self.exhausted.add(name)

    def reset(self) -> None:
        """Clear exhausted state (e.g. after waiting for quota to renew)."""
        with self._lock:
            self.exhausted.clear()

    @property
    def is_fully_exhausted(self) -> bool:
        """True when every credential in the pool is exhausted."""
        if not self.credentials:
            return True
        with self._lock:
            return all(c.name in self.exhausted for c in self.credentials)

    @property
    def active_count(self) -> int:
        """Number of credentials not yet exhausted."""
        return sum(1 for c in self.credentials if c.name not in self.exhausted)

    @property
    def total_count(self) -> int:
        """Total number of credentials in the pool."""
        return len(self.credentials)

    @property
    def exhausted_names(self) -> tuple[str, ...]:
        """Names of exhausted credentials (preserves credential order)."""
        return tuple(c.name for c in self.credentials if c.name in self.exhausted)

    @property
    def active_names(self) -> tuple[str, ...]:
        """Names of non-exhausted credentials (preserves credential order)."""
        return tuple(c.name for c in self.credentials if c.name not in self.exhausted)


class SearchEngine(abc.ABC):
    """Base class for all search engines.

    Concrete subclasses must implement :meth:`_raw_search` (the actual
    query logic). The default :meth:`search` adds a configurable
    per-call timeout (via :class:`concurrent.futures.ThreadPoolExecutor`)
    and a uniform post-filter step (via :func:`smart_filter_urls`).

    Subclasses that want to return their own exception types should
    raise :class:`SearchEngineQuotaError` for quota conditions — the
    orchestrator uses that signal to decide between "switch engines"
    and "give up".
    """

    #: The engine's canonical name (used in logs, the audit tag, etc.).
    name: str = "abstract"

    def __init__(self, *, timeout_s: float = 30.0, topk_filter: int | None = None) -> None:
        self.timeout_s = timeout_s
        self.topk_filter = topk_filter

    # -- Public API -----------------------------------------------------

    def search(self, query: str, topk: int) -> list[SearchHit]:
        """Synchronous search with timeout + post-filter.

        Args:
            query: search query string.
            topk:  desired number of URLs.

        Returns:
            List of :class:`SearchHit` (post-filter, length ≤ topk).

        Raises:
            SearchEngineTimeoutError: if the engine exceeds ``timeout_s``.
            SearchEngineQuotaError: if the engine signals quota exhaustion.
            SearchEngineError: for any other engine-level failure.
        """
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(self._raw_search, query, topk)
            try:
                hits = fut.result(timeout=self.timeout_s)
            except (asyncio.TimeoutError, _FutTimeoutError) as err:
                # Both asyncio.TimeoutError and concurrent.futures.TimeoutError
                # are aliases for the builtin TimeoutError in Python 3.11+.
                raise SearchEngineTimeoutError(f"{self.name}: timeout after {self.timeout_s}s") from err

        return self._post_filter(hits, topk)

    async def asearch(self, query: str, topk: int) -> list[SearchHit]:
        """Async wrapper around :meth:`search`.

        Exists so the orchestrator can ``await`` it without dragging in
        another executor. Most engines don't need async-native
        networking, but exposing it lets us swap engines later.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.search, query, topk)

    # -- Helpers --------------------------------------------------------

    def _post_filter(self, hits: list[SearchHit], topk: int) -> list[SearchHit]:
        """Apply the project-wide smart URL filter to engine output.

        Args:
            hits: raw engine hits.
            topk: desired final count.

        Returns:
            Filtered hits (length ≤ topk). URL filter stats are
            available via :func:`smart_filter_urls`'s return contract;
            we re-run with ``verbose=False`` to keep the engine API
            quiet by default. Callers wanting diagnostics should hit
            :func:`smart_filter_urls` directly.
        """
        urls = [h.url for h in hits]
        kept = smart_filter_urls(urls, keep_per_domain=self.topk_filter, verbose=False)
        kept_set = set(kept)
        out = [h for h in hits if h.url in kept_set or canonicalize_keep(h.url) in kept_set]
        return out[:topk]

    # -- Subclass hook --------------------------------------------------

    @abc.abstractmethod
    def _raw_search(self, query: str, topk: int) -> list[SearchHit]:
        """Subclass-implemented: do the actual network call.

        Implementations should raise :class:`SearchEngineQuotaError`
        for quota conditions (the orchestrator handles engine
        switching based on this). For everything else, raise
        :class:`SearchEngineError`.
        """


def canonicalize_keep(url: str) -> str:
    """Stable host+path lower form for inclusion checks after filter."""
    from deep_dive.filters.canonical import canonicalize_url

    return canonicalize_url(url)


# ---------------------------------------------------------------------------
# Multi-key engine
# ---------------------------------------------------------------------------


class MultiKeyEngine(SearchEngine):
    """Search engine that can rotate across multiple credentials.

    Subclasses implement :meth:`_try_with_credential` to perform **one**
    search attempt using a single credential. This base class handles
    sequential rotation: on a retryable error (see :attr:`RETRYABLE_ERRORS`),
    the credential is marked exhausted in the pool and the next one is
    tried. Only when **all** credentials fail does :class:`SearchEngineQuotaError`
    propagate to the orchestrator.

    Audit (per-call, thread-local so concurrent tasks sharing one engine
    instance don't clobber each other)::

        engine.last_used_credential_name   # str | None
        engine.last_tried_credentials     # list[str]
        engine.last_exhausted_credentials # list[str]

    Read these **right after** :meth:`search` returns, before any other
    thread calls search() on the same engine.

    Example::

        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="..."),
            EngineCredential(name="KEY2", key="..."),
        ])
        engine = TavilyEngine(pool=pool)
        hits = engine.search("算电协同", 10)
        audit = engine.get_audit()  # {'key_used': 'KEY1', 'keys_tried': ['KEY1'], ...}
    """

    #: Error types that trigger rotation to the next credential. Subclasses
    #: may override (e.g. MMXEngine drops ``SearchEngineAuthError`` since
    #: the CLI has no auth concept).
    RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
        SearchEngineQuotaError,
        SearchEngineAuthError,
        SearchEngineNetworkError,
        SearchEngineTimeoutError,
    )

    def __init__(
        self,
        *,
        pool: EngineAccountPool,
        timeout_s: float = 30.0,
        topk_filter: int | None = None,
    ) -> None:
        super().__init__(timeout_s=timeout_s, topk_filter=topk_filter)
        self.pool = pool
        # Per-call audit (thread-local so concurrent tasks sharing this
        # engine instance don't clobber each other's audit dict).
        # NOTE: ``threading.local()`` attrs are per-thread, so we can't
        # pre-init here for the main thread and expect worker threads to
        # see them. All access is ``hasattr``-guarded.
        self._audit_tls = threading.local()

    # -- Audit helpers ---------------------------------------------------
    #
    # ``threading.local()`` attrs are per-thread; a worker thread that
    # touches ``self._audit_tls.tried`` before ``_reset_audit`` ran on
    # that thread will hit ``AttributeError``. Every mutation checks
    # ``hasattr`` first; every read uses ``getattr`` with a default.

    def _reset_audit(self) -> None:
        self._audit_tls.used = None
        self._audit_tls.tried = []
        self._audit_tls.exhausted = []

    def _audit_used(self, name: str | None) -> None:
        self._audit_tls.used = name

    def _audit_tried(self, name: str) -> None:
        if not hasattr(self._audit_tls, "tried"):
            self._audit_tls.tried = []
        self._audit_tls.tried.append(name)

    def _audit_exhausted(self, name: str) -> None:
        if not hasattr(self._audit_tls, "exhausted"):
            self._audit_tls.exhausted = []
        self._audit_tls.exhausted.append(name)

    def get_audit(self) -> dict[str, Any]:
        """Snapshot of the last :meth:`search` call's credential rotation.

        Returns:
            Dict with ``key_used`` (str | None), ``keys_tried`` (list),
            ``keys_exhausted`` (list). Empty lists if no search has run
            yet on this thread.
        """
        return {
            "key_used": getattr(self._audit_tls, "used", None),
            "keys_tried": list(getattr(self._audit_tls, "tried", [])),
            "keys_exhausted": list(getattr(self._audit_tls, "exhausted", [])),
        }

    # -- Search override -------------------------------------------------

    def search(self, query: str, topk: int) -> list[SearchHit]:
        """Synchronous search with timeout + post-filter.

        We intentionally **skip** the :class:`SearchEngine` base class's
        per-call :class:`ThreadPoolExecutor` and call :meth:`_raw_search`
        directly. The executor runs the search in a different thread from
        the caller, but our audit is **thread-local** (so concurrent tasks
        sharing one engine don't clobber each other), and writes/reads
        happen on the caller's thread. Routing the call through an
        internal executor would put the writes in the executor thread and
        the reads in the caller thread, leaving the caller with an empty
        audit.

        The trade-off is that :class:`MultiKeyEngine` no longer enforces
        ``timeout_s`` itself. Callers using the engine directly should
        apply their own timeout (the :class:`Orchestrator` does this via
        its :class:`ThreadPoolExecutor` in ``_dispatch_parallel``).
        """
        self._reset_audit()
        return self._post_filter(self._raw_search(query, topk), topk)

    # -- Rotation logic --------------------------------------------------

    def _raw_search(self, query: str, topk: int) -> list[SearchHit]:
        """Iterate through the pool until one credential succeeds.

        Subclasses override this if they want non-rotation semantics, but
        the default implementation handles the common case: try each
        credential in order, retry on RETRYABLE_ERRORS.
        """
        if not self.pool.credentials:
            raise SearchEngineError(f"{self.name}: no credentials configured (empty pool)")
        if self.pool.is_fully_exhausted:
            raise SearchEngineQuotaError(f"{self.name}: all credentials already exhausted before query")
        while True:
            cred = self.pool.next_active()
            if cred is None:
                tried_snapshot = list(getattr(self._audit_tls, "tried", []))
                raise SearchEngineQuotaError(
                    f"{self.name}: all credentials exhausted "
                    f"({len(self.pool.exhausted_names)}/{len(self.pool.credentials)}); "
                    f"tried: {tried_snapshot}"
                )
            self._audit_tried(cred.name)
            try:
                hits = self._try_with_credential(cred, query, topk)
            except self.RETRYABLE_ERRORS:
                self.pool.mark_exhausted(cred.name)
                self._audit_exhausted(cred.name)
                continue
            self._audit_used(cred.name)
            return hits

    @abc.abstractmethod
    def _try_with_credential(
        self,
        cred: EngineCredential,
        query: str,
        topk: int,
    ) -> list[SearchHit]:
        """Subclass-implemented: perform one search attempt with ``cred``.

        Raise any subclass of :attr:`RETRYABLE_ERRORS` to trigger rotation
        to the next credential. Raise any other exception to abort with
        that exception propagating up.
        """


__all__ = [
    "SearchEngine",
    "MultiKeyEngine",
    "EngineCredential",
    "EngineAccountPool",
    "SearchEngineError",
    "SearchEngineQuotaError",
    "SearchEngineTimeoutError",
    "SearchEngineAuthError",
    "SearchEngineNetworkError",
]
