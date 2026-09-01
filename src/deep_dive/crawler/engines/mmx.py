"""MMX search engine backend.

Wraps the external ``mmx`` CLI (provided by MiniMax Token Plan). The CLI
is invoked as a subprocess with a hard timeout. The output is JSON.

Multi-invocation
----------------

Each :class:`EngineCredential` in the pool represents one MMX invocation
profile with its own ``(path, env, args)``. The pool rotates through
them in order, retrying on quota / timeout / non-zero exit. This lets
users configure:

* Multiple MMX binaries (e.g., one per account or organisation).
* Different ``env`` overrides per invocation (e.g.,
  ``MMX_API_KEY=work-key`` for one, ``MMX_API_KEY=personal-key`` for another).
* Different CLI ``args`` per invocation (e.g., ``--profile=work``).

The default constructor with no arguments creates a 1-credential pool
using ``shutil.which("mmx")`` and the inherited environment.

Why subprocess + thread-pool timeout?
-------------------------------------

On Windows, ``subprocess.run(timeout=...)`` is reliable, but on some
older Python + Windows combos it can hang the parent on stdio close.
We run the subprocess inside a single-worker ThreadPool and use the
executor-level timeout for an extra layer of safety.

Quota detection
---------------

We look at the combined ``stdout + stderr`` for any of the known
:data:`deep_dive.constants.QUOTA_KEYWORDS`. If found, we raise
:class:`SearchEngineQuotaError` so the :class:`MultiKeyEngine` base
class knows to mark this credential exhausted and try the next one.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Mapping
from typing import Any

from deep_dive.constants import QUOTA_KEYWORDS, TAG_MMX_ERR, TAG_MMX_OK, TAG_MMX_QUOTA
from deep_dive.logging_setup import safe_print
from deep_dive.types import SearchHit

from .base import (
    EngineAccountPool,
    EngineCredential,
    MultiKeyEngine,
    SearchEngineError,
    SearchEngineQuotaError,
    SearchEngineTimeoutError,
)

# Cache the resolved mmx path (avoid re-running shutil.which per query).
# Sentinel: ``False`` means "not yet searched"; ``str`` = found path;
# ``None`` = searched and not on PATH. Guarded by ``_MMX_PATH_LOCK``
# so concurrent calls from worker threads don't race.
_MMX_PATH_CACHE: str | None | bool = False
_MMX_PATH_LOCK = threading.Lock()


def _resolve_mmx_path() -> str | None:
    """Resolve and cache the ``mmx`` executable path.

    Returns:
        Absolute path to the ``mmx`` executable, or ``None`` if not
        found on ``PATH``.
    """
    global _MMX_PATH_CACHE
    if _MMX_PATH_CACHE is False:
        with _MMX_PATH_LOCK:
            # Double-checked locking: avoid running ``shutil.which``
            # more than once across the whole process.
            if _MMX_PATH_CACHE is False:
                _MMX_PATH_CACHE = shutil.which("mmx")
    return _MMX_PATH_CACHE  # type: ignore[return-value]


def _looks_like_quota(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(kw.lower() in low for kw in QUOTA_KEYWORDS)


def _truncate_for_log(query: str, max_len: int = 50) -> str:
    """Smart log-friendly query truncation.

    Old behavior was ``query[:30]`` which silently truncated different queries
    to identical-looking prefixes (e.g. three English variants of
    "<book-title-en>" all showed the same 30 chars). Now we
    show up to ``max_len`` chars, with an explicit ``…`` indicator when cut.
    """
    if len(query) <= max_len:
        return query
    return f"{query[:max_len]}…"


class MMXEngine(MultiKeyEngine):
    """MMX-backed search engine with multi-invocation rotation.

    Each credential in the pool represents one ``(path, env, args)``
    invocation profile. The engine retries on quota, timeout, and
    non-zero subprocess exit (treating them as credential-level failures
    — the current binary / profile is "spent", try the next one).

    Non-retryable errors:
        - ``FileNotFoundError`` (mmx binary missing — installation problem)
        - JSON decode errors (bad CLI output — version mismatch etc.)
    These propagate as :class:`SearchEngineError` and stop the rotation.
    """

    name = "mmx"

    def __init__(
        self,
        *,
        pool: EngineAccountPool | None = None,
        invocations: list[dict[str, Any]] | None = None,
        timeout_s: float = 35.0,
        **kwargs: Any,
    ) -> None:
        """Construct an MMX engine.

        Args:
            pool: explicit :class:`EngineAccountPool`. If given, all
            other invocation arguments are ignored.
            invocations: list of dicts with keys ``name`` / ``path`` /
            ``env`` / ``args``. Each becomes one credential in the pool.
            timeout_s: per-search budget across all invocations.

        If neither ``pool`` nor ``invocations`` is given, builds a
        single-credential pool using ``shutil.which("mmx")``.
        """
        if pool is None:
            pool = self._build_pool(invocations=invocations)
        super().__init__(pool=pool, timeout_s=timeout_s, **kwargs)

    # ------------------------------------------------------------------
    # Pool construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_pool(
        *,
        invocations: list[dict[str, Any]] | None,
    ) -> EngineAccountPool:
        """Build a pool from explicit invocations, or a single default."""
        if invocations:
            creds = [
                EngineCredential(
                    name=inv.get("name") or f"mmx-{i + 1}",
                    path=inv.get("path"),
                    env=inv.get("env"),
                    args=tuple(inv.get("args") or ()),
                )
                for i, inv in enumerate(invocations)
            ]
            return EngineAccountPool(creds, name="mmx")
        # Default: 1 credential, ``shutil.which("mmx")`` + inherited env.
        return EngineAccountPool(
            [EngineCredential(name="mmx-default", path=None, env=None, args=())],
            name="mmx",
        )

    # ------------------------------------------------------------------
    # Per-credential search attempt
    # ------------------------------------------------------------------

    def _try_with_credential(
        self,
        cred: EngineCredential,
        query: str,
        topk: int,
    ) -> list[SearchHit]:
        """Run one MMX invocation using ``cred``.

        Raises:
            SearchEngineQuotaError: quota keyword in stdout/stderr.
            SearchEngineTimeoutError: subprocess / executor timeout.
            SearchEngineError: bad JSON, binary missing, or other
                permanent failure. Non-retryable.
        """
        mmx_path = cred.path or _resolve_mmx_path()
        if not mmx_path:
            # Binary missing is a permanent configuration error — not a
            # credential-level failure. Surface as plain
            # SearchEngineError so the rotation stops.
            safe_print(
                f"{TAG_MMX_ERR} {cred.name}: mmx CLI not found (path={cred.path!r}); skipping '{query[:30]}'"
            )
            raise SearchEngineError(f"{self.name}: mmx CLI not found on PATH (credential={cred.name})")

        cmd = [
            mmx_path,
            "search",
            "query",
            "--q",
            query,
            "--output",
            "json",
            "--quiet",
        ]
        cmd.extend(cred.args)

        # Build per-invocation env: inherit parent env, then apply overrides.
        run_env: Mapping[str, str] | None = None
        if cred.env:
            run_env = dict(os.environ)
            run_env.update(cred.env)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout_s,
                    env=run_env,
                )
                # Give the executor a small grace period beyond the
                # subprocess timeout — handles the Windows stdio-close
                # race documented in legacy comments.
                res = fut.result(timeout=self.timeout_s + 5)
        except (concurrent.futures.TimeoutError, subprocess.TimeoutExpired) as e:
            safe_print(f"{TAG_MMX_ERR} {cred.name} timeout after {self.timeout_s}s: '{query[:30]}'")
            raise SearchEngineTimeoutError(f"mmx {cred.name} timeout: {query}") from e
        except FileNotFoundError as e:
            safe_print(f"{TAG_MMX_ERR} {cred.name} executable disappeared: {e}")
            raise SearchEngineError(f"mmx {cred.name} executable gone: {e}") from e
        except Exception as e:
            safe_print(f"{TAG_MMX_ERR} {cred.name} '{query[:30]}': {type(e).__name__}: {e}")
            raise SearchEngineError(str(e)) from e

        combined = (res.stdout or "") + (res.stderr or "")
        if _looks_like_quota(combined):
            safe_print(f"{TAG_MMX_QUOTA} {cred.name} quota exhausted: '{query[:30]}'")
            raise SearchEngineQuotaError(f"mmx {cred.name} quota exhausted")

        if res.returncode != 0:
            combined_err = (res.stderr or "") + (res.stdout or "")
            # Distinguish quota from genuine non-zero exits.
            # Quota messages come from the mmx CLI itself or are echoed
            # in stderr; they contain one of the well-known keywords.
            if _looks_like_quota(combined_err):
                safe_print(
                    f"{TAG_MMX_QUOTA} {cred.name} exit={res.returncode} quota-like stderr: '{query[:30]}'"
                )
                raise SearchEngineQuotaError(f"mmx {cred.name} quota (exit {res.returncode})")
            # Otherwise this is a genuine subprocess failure (bad query,
            # version mismatch, network proxy corruption, etc.). Surface
            # as a permanent error so we don't waste rotations on a
            # broken credential — the user needs to fix the CLI, not
            # burn through the key pool.
            safe_print(
                f"{TAG_MMX_ERR} {cred.name} exit={res.returncode} "
                f"'{query[:30]}': stderr={(res.stderr or '')[:200]}"
            )
            raise SearchEngineError(f"mmx {cred.name} exit {res.returncode}: {query}")

        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError as e:
            # Bad JSON from mmx is a real signal that something is wrong
            # with the CLI (corrupt output, version mismatch, network
            # proxy corruption, etc.). Surface as plain SearchEngineError
            # so we don't waste rotations on a version-mismatched binary.
            safe_print(f"{TAG_MMX_ERR} {cred.name} bad JSON: {e}; stdout_head={(res.stdout or '')[:100]}")
            raise SearchEngineError(f"mmx {cred.name} bad JSON: {e}") from e

        organic = data.get("organic")
        if not isinstance(organic, list):
            return []

        hits: list[SearchHit] = []
        for item in organic:
            url = item.get("link")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title", "") or "",
                    snippet=item.get("snippet", "") or "",
                    engine=self.name,
                )
            )
        safe_print(f"{TAG_MMX_OK} {cred.name} '{_truncate_for_log(query, 50)}' -> {len(hits)} URLs")
        return hits[:topk]


__all__ = ["MMXEngine", "_resolve_mmx_path"]
