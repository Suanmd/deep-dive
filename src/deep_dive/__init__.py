"""deep-dive — Deep research engine for agents.

Plan-driven deep-research engine: multi-perspective parallel search +
multi-language expansion + site targeting + full-text scraping + dedup
aggregation + structured Markdown report output.

Public API entry points (stable):

    from deep_dive import Orchestrator, Config, TaskResult, AggregatedResult
    from deep_dive.types import ResearchPlan
    from deep_dive.filters import canonicalize_url, smart_filter_urls
    from deep_dive.crawler.engines import MMXEngine, TavilyEngine
    from deep_dive.crawler.fetchers import PlaywrightFetcher, CloudScraperFetcher

For most users, the CLI is enough::

    python -m deep_dive --query "..." --depth normal
    deep-dive --query "..." --depth normal

For programmatic use, see :class:`deep_dive.orchestrator.Orchestrator`.
"""

from __future__ import annotations

from .config import Config, load_config
from .orchestrator import Orchestrator, auto_plan
from .types import (
    AggregatedResult,
    CrawlResult,
    EngineKind,
    FetchResult,
    FetchStatus,
    MatrixRow,
    RelevanceVerdict,
    ResearchPlan,
    SearchHit,
    TaskResult,
    TaskStatus,
    detect_kind,
)

__version__ = "1.0.0"

__all__ = [
    # Version
    "__version__",
    # Config
    "Config",
    "load_config",
    # Orchestrator
    "Orchestrator",
    # Types
    "AggregatedResult",
    "CrawlResult",
    "EngineKind",
    "FetchResult",
    "FetchStatus",
    "MatrixRow",
    "RelevanceVerdict",
    "ResearchPlan",
    "SearchHit",
    "TaskResult",
    "TaskStatus",
    # Smart auto-detection helpers
    "auto_plan",
    "detect_kind",
]
