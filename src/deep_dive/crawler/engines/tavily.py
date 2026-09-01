"""Tavily search engine backend.

Wraps the ``tavily-python`` SDK. Supports N-key rotation
via :class:`MultiKeyEngine` and :class:`EngineAccountPool`.

Multi-key design
----------------

* Credentials are passed as a :class:`EngineAccountPool`. The constructor
  still accepts the legacy ``api_key`` / ``api_key_backup`` pair and a
  new ``keys=`` list, but the canonical way is to pass ``pool=``.
* Multiple env vars are honoured (in order):
    1. ``TAVILY_API_KEYS`` — comma-separated list (NEW)
    2. ``TAVILY_API_KEY_BACKUP`` — single backup key (backwards compat)
    3. ``TAVILY_API_KEY`` — single key (backwards compat)
* On any of quota / auth / network / timeout, the current credential is
  marked exhausted and the next one in the pool is tried. Only after
  **all** credentials fail does ``SearchEngineQuotaError`` propagate.
* Per-call audit (``key_used``, ``keys_tried``, ``keys_exhausted``)
  is exposed via :meth:`MultiKeyEngine.get_audit` for the orchestrator
  to record into ``TaskResult.extra``.
"""

from __future__ import annotations

import concurrent.futures
import os
from typing import Any

from deep_dive.constants import TAG_TAVILY_ERR, TAG_TAVILY_OK
from deep_dive.logging_setup import safe_print
from deep_dive.types import SearchHit

from .base import (
    EngineAccountPool,
    EngineCredential,
    MultiKeyEngine,
    SearchEngineAuthError,
    SearchEngineError,
    SearchEngineNetworkError,
    SearchEngineQuotaError,
    SearchEngineTimeoutError,
)

try:
    from tavily import TavilyClient

    _HAS_TAVILY = True
except ImportError:  # pragma: no cover — tavily-python is a hard dep
    _HAS_TAVILY = False


# Per-call timeout (within an attempt); the SearchEngine-level
# ``timeout_s`` covers the total time across keys.
_PER_KEY_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

_QUOTA_NEEDLES = (
    "exceeds",
    "usage limit",
    "quota",
    "rate limit",
    "too many requests",
)

_AUTH_NEEDLES = (
    "unauthorized",
    "forbidden",
    "invalid api key",
    "401",
    "403",
    "api key is invalid",
)

_NETWORK_NEEDLES = (
    "ssl",
    "connection",
    "dns",
    "max retries",
    "network",
    "timeout",
    "eof occurred",
)


def _classify_error(message: str) -> type[SearchEngineError]:
    """Map a Tavily error message string to one of our error types.

    Used by :meth:`TavilyEngine._try_with_credential` to decide which
    exception to raise; :class:`MultiKeyEngine` then uses that signal
    to decide whether to rotate to the next credential.

    Args:
        message: lower-cased error string from Tavily / httpx / etc.

    Returns:
        One of :class:`SearchEngineQuotaError`,
        :class:`SearchEngineAuthError`,
        :class:`SearchEngineNetworkError`,
        or :class:`SearchEngineError` (unclassified — non-retryable).
    """
    msg = (message or "").lower()
    if any(k in msg for k in _QUOTA_NEEDLES):
        return SearchEngineQuotaError
    if any(k in msg for k in _AUTH_NEEDLES):
        return SearchEngineAuthError
    if any(k in msg for k in _NETWORK_NEEDLES):
        return SearchEngineNetworkError
    return SearchEngineError


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TavilyEngine(MultiKeyEngine):
    """Tavily-backed search engine with N-key auto rotation."""

    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_backup: str | None = None,
        keys: list[str] | None = None,
        pool: EngineAccountPool | None = None,
        timeout_s: float = 60.0,
        **kwargs: Any,
    ) -> None:
        """Construct a Tavily engine.

        Args:
            api_key: legacy single-key API. Kept for backwards compat;
                equivalent to ``keys=[api_key]``.
            api_key_backup: legacy backup key. Kept for backwards compat;
                equivalent to ``keys=[api_key, api_key_backup]``.
            keys: list of API keys (NEW). Each key becomes one
                credential in the pool, named ``KEY1``, ``KEY2``, ..., ``KEYN`` (supports arbitrary N keys, not limited to 2)
            pool: explicit credential pool. If given, all other
                arguments are ignored.
            timeout_s: total per-search budget across all keys.

        Env-var fallbacks (used only if no explicit args):

        * ``TAVILY_API_KEYS``: comma-separated list of API keys.
        * ``TAVILY_API_KEY_BACKUP``: single backup key.
        * ``TAVILY_API_KEY``: single primary key.
        """
        if pool is None:
            pool = self._build_pool_from_args(
                api_key=api_key,
                api_key_backup=api_key_backup,
                keys=keys,
            )
        super().__init__(pool=pool, timeout_s=timeout_s, **kwargs)

    # ------------------------------------------------------------------
    # Pool construction (multiple input shapes supported)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pool_from_args(
        *,
        api_key: str | None,
        api_key_backup: str | None,
        keys: list[str] | None,
    ) -> EngineAccountPool:
        """Merge programmatic args + env vars into one pool."""
        seen_keys: set[str] = set()
        creds: list[EngineCredential] = []

        def _add(key: str | None, slot_name: str) -> None:
            if not key:
                return
            k = key.strip()
            if not k or k in seen_keys:
                return
            seen_keys.add(k)
            creds.append(EngineCredential(name=slot_name, key=k))

        # 1. Explicit ``keys=`` list wins on first match (CLI / programmatic)
        if keys:
            for i, k in enumerate(keys, 1):
                _add(k, f"KEY{i}")
        # 2. Legacy single key + backup
        _add(api_key, "KEY1")
        _add(api_key_backup, "KEY2")
        # 3. Env var: TAVILY_API_KEYS (comma-separated)
        env_keys = os.environ.get("TAVILY_API_KEYS", "").strip()
        if env_keys:
            for i, k in enumerate(env_keys.split(","), len(creds) + 1):
                _add(k, f"KEY{i}")
        # 4. Env var: TAVILY_API_KEY_BACKUP (backwards compat)
        _add(
            os.environ.get("TAVILY_API_KEY_BACKUP", ""),
            f"KEY{len(creds) + 1}",
        )
        # 5. Env var: TAVILY_API_KEY (backwards compat)
        _add(
            os.environ.get("TAVILY_API_KEY", ""),
            f"KEY{len(creds) + 1}",
        )

        return EngineAccountPool(creds, name="tavily")

    # ------------------------------------------------------------------
    # Backwards-compat properties
    # ------------------------------------------------------------------

    @property
    def has_any_key(self) -> bool:
        """True if the pool has at least one credential configured."""
        return self.pool.total_count > 0

    @property
    def api_key(self) -> str | None:
        """Legacy accessor for backwards compat.

        Returns the first credential's key, or ``None``.
        """
        for c in self.pool.credentials:
            if c.key:
                return c.key
        return None

    @property
    def api_key_backup(self) -> str | None:
        """Legacy accessor: returns the second credential's key, or None."""
        creds = list(self.pool.credentials)
        if len(creds) >= 2 and creds[1].key:
            return creds[1].key
        return None

    # ------------------------------------------------------------------
    # Per-credential search attempt
    # ------------------------------------------------------------------

    def _try_with_credential(
        self,
        cred: EngineCredential,
        query: str,
        topk: int,
    ) -> list[SearchHit]:
        """Run one Tavily search using ``cred.key``.

        Raises:
            SearchEngineQuotaError / AuthError / NetworkError / TimeoutError:
                triggers rotation to the next credential in the pool.
            SearchEngineError: unknown failure; propagates without retry.
        """
        if not _HAS_TAVILY:
            # Library missing — no point rotating. Surface as permanent
            # error so the user sees a clear message.
            raise SearchEngineError(f"{self.name}: tavily-python SDK is not installed")
        if not cred.key:
            # Credential missing a key — treat as auth error so it
            # rotates (next cred may have a valid key).
            raise SearchEngineAuthError(f"{self.name}: credential {cred.name!r} has no API key")

        try:
            client = TavilyClient(api_key=cred.key)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(client.search, query=query, max_results=topk)
                res = fut.result(timeout=_PER_KEY_TIMEOUT_S)
        except concurrent.futures.TimeoutError as e:
            safe_print(f"{TAG_TAVILY_ERR} {cred.name} timeout after {_PER_KEY_TIMEOUT_S}s: '{query[:30]}'")
            raise SearchEngineTimeoutError(f"{self.name}: {cred.name} timeout") from e
        except Exception as e:
            # Classify the error and raise the matching typed exception.
            # MultiKeyEngine will catch retryable types and rotate.
            err_cls = _classify_error(str(e))
            err_msg = f"{self.name}: {cred.name}: {type(e).__name__}: {e}"
            safe_print(f"{TAG_TAVILY_ERR} {cred.name} '{query[:30]}': {err_msg}")
            raise err_cls(err_msg) from e

        # Success path
        results = res.get("results", []) if isinstance(res, dict) else []
        hits: list[SearchHit] = []
        for item in results:
            url = item.get("url")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title", "") or "",
                    snippet=item.get("content", "") or "",
                    engine=self.name,
                )
            )
        safe_print(f"{TAG_TAVILY_OK} {cred.name} '{query[:30]}' -> {len(hits)} URLs")
        return hits[:topk]


__all__ = ["TavilyEngine"]
