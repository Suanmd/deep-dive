"""Search-engine backends.

Every search engine implements :class:`SearchEngine` and returns a
sequence of :class:`~deep_dive.types.SearchHit`.

Currently implemented:

* :class:`MMXEngine` — uses the external ``mmx`` CLI (provided by
  MiniMax Token Plan). Free inside the plan, fast on Chinese.
* :class:`TavilyEngine` — wraps the ``tavily-python`` SDK. Has a
  primary-key + backup-key auto-fallback chain.

Adding a new engine is a matter of:

    1. Subclassing :class:`SearchEngine`.
    2. Implementing :meth:`SearchEngine.search` (sync) **or**
       :meth:`SearchEngine.asearch` (async).
    3. Registering in :func:`get_engine` if you want it addressable by
       ``--search-engine=foo``.
"""

from __future__ import annotations

from .base import SearchEngine, SearchEngineError, SearchEngineQuotaError, SearchEngineTimeoutError
from .duckduckgo import DuckDuckGoEngine
from .mmx import MMXEngine
from .tavily import TavilyEngine

__all__ = [
    "SearchEngine",
    "SearchEngineError",
    "SearchEngineQuotaError",
    "SearchEngineTimeoutError",
    "DuckDuckGoEngine",
    "MMXEngine",
    "TavilyEngine",
    "get_engine",
]


_ENGINE_REGISTRY: dict[str, type[SearchEngine]] = {
    "mmx": MMXEngine,
    "tavily": TavilyEngine,
    "duckduckgo": DuckDuckGoEngine,
}


def get_engine(name: str, **kwargs) -> SearchEngine:
    """Construct an engine by name.

    Args:
        name: one of ``"mmx"`` / ``"tavily"``. Unknown names raise
            :class:`SearchEngineError`.
        **kwargs: forwarded to the engine's constructor.

    Returns:
        A ready-to-use :class:`SearchEngine` instance.
    """
    cls = _ENGINE_REGISTRY.get(name.lower())
    if cls is None:
        raise SearchEngineError(
            f"Unknown engine: {name!r}. Available: {sorted(_ENGINE_REGISTRY)}"
        )
    return cls(**kwargs)


def register_engine(name: str, cls: type[SearchEngine]) -> None:
    """Register a custom engine. Useful for plugin-style extensions."""
    _ENGINE_REGISTRY[name.lower()] = cls
