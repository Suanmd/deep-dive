"""Search-matrix orchestrator.

The orchestrator is the entry point for one full ``deep-dive`` run.
It:

1. Builds the search matrix from the user's query + ``Config``.
2. Dispatches tasks in parallel (with the watchdog + heartbeat).
3. Aggregates results and persists a ``summary.json``.
4. Runs :func:`auto_rescue_raw` if dedup == 0.
5. Builds the ``report.md`` (when ``--no-report`` is not set).
6. Appends the Capy summary section (when ``--no-capy`` is not set).

Why split this out of the legacy monolith?
-------------------------------------------

The legacy ``deep_search.py`` was 57 KB and conflated five distinct
responsibilities (CLI parsing, search-matrix construction, parallel
task dispatch, result aggregation, and report generation). In the new
package:

* :mod:`deep_dive.cli` — argparse + log setup only.
* :mod:`deep_dive.orchestrator` — this file: matrix + parallel dispatch.
* :mod:`deep_dive.aggregator` — cross-task dedup + rescue trigger.
* :mod:`deep_dive.rescue` — auto_rescue_raw.
* :mod:`deep_dive.reporting.builder` — markdown report.
* :mod:`deep_dive.reporting.capy_summary` — Capy summary.

This means each concern can be unit-tested in isolation, and a power
user can drop the CLI and call ``Orchestrator`` directly from their own
agent loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deep_dive.aggregator import Aggregator, AggregatedResult
from deep_dive.config import Config
from deep_dive.constants import (
    DEFAULT_GLOBAL_TIMEOUT_S,
    DEFAULT_HEARTBEAT_INTERVAL_S,
    DEFAULT_TASK_TIMEOUT_S,
    LOWQ_DOMAINS,
    MEDIUM_ALTERNATIVES,
    TAG_DONE,
    TAG_ERR,
    TAG_FIRE,
    TAG_HEARTBEAT,
    TAG_INFO,
    TAG_OK,
    TAG_TIME,
    TAG_WARN,
)
from deep_dive.crawler.engines import DuckDuckGoEngine, MMXEngine, TavilyEngine
from deep_dive.crawler.engines.base import SearchEngine, SearchEngineError, SearchEngineQuotaError
from deep_dive.crawler.fetchers import CloudScraperFetcher, PlaywrightFetcher
from deep_dive.crawler.pipeline import CrawlPipeline, PipelineConfig
from deep_dive.local_langs import detect_local_langs
from deep_dive.logging_setup import safe_print
from deep_dive.query_classifier import (
    kind_from_plan,
    pick_site_query,
    sites_from_plan,
    template_from_plan,
)
from deep_dive.query_variants import generate_variants_from_plan, variant_note_label
from deep_dive.relevance import is_query_irrelevant
from deep_dive.reporting.builder import build_report
from deep_dive.reporting.capy_summary import append_capy_section
from deep_dive.rescue import auto_rescue_raw
from deep_dive.types import (
    CrawlResult,
    FetchResult,
    FetchStatus,
    MatrixRow,
    QueryKind,
    ResearchPlan,
    TaskResult,
    TaskStatus,
    detect_kind,
)


def auto_plan(query: str) -> ResearchPlan:
    """Generate a smart minimal ResearchPlan when the caller does not supply one.

    Auto-decisions:
      - ``kind``: keyword-based detection (see :func:`detect_kind`).
      - ``language_priority``: Chinese chars detected → ``zh-primary``,
        else ``en-primary``.
      - ``variants``: kind-aware — tech uses paper/limitation/benchmark/
        ablation suffixes; movies/TV use review/themes/reception/sequel
        angles; other kinds use the generic 详解/局限/综述/对比 suffixes.
      - ``english_search_terms``: for tech queries **always** non-empty
        (even when the user typed Chinese), because authoritative AI/CS
        primary sources (arXiv, GitHub, Papers with Code) are
        English-first. For non-tech Chinese queries the list is empty
        (no LLM to translate); for non-Chinese non-tech queries it's
        the query verbatim.
      - ``target_sites``: for tech queries, defaults to
        ``("arxiv.org", "github.com", "paperswithcode.com")`` — the
        three primary-source hubs the user almost always wants for
        AI/CS topics. For other kinds the list is empty (let search
        engines find content organically).
      - ``relevance_threshold``: 0.40 (matches the keyword-score gate
        the relevance module uses for two-stage filtering).

    An LLM is still welcome to override any of these via ``--plan``.
    """
    is_chinese = any("\u4e00" <= c <= "\u9fff" for c in query)
    kind = detect_kind(query)
    is_tech = kind == "tech"

    # Primary-source sites for tech queries. arXiv.org is the canonical
    # preprint host for AI/CS; github.com hosts reference implementations
    # and benchmark code; paperswithcode.com links papers to their code
    # + benchmark results. The matrix builder emits site-targeted rows
    # for the first N of these (N depends on depth: 2 for normal, 3 for
    # full, 0 for quick).
    target_sites: tuple[str, ...] = (
        ("arxiv.org", "github.com", "paperswithcode.com") if is_tech else ()
    )

    # English baseline task: keep it for tech queries (so we surface
    # arXiv papers even when the user typed Chinese) and for all
    # non-Chinese queries. Drop it only for non-tech Chinese queries.
    if is_tech or not is_chinese:
        english_terms: tuple[str, ...] = (query,)
    else:
        english_terms = ()

    # Kind-aware variant suffixes.
    if is_tech:
        if is_chinese:
            variants = {
                "refined": query + " 论文 解读",
                "critique": query + " 局限 批评",
                "academic": query + " 综述 benchmark",
                "primary": query,
                "comparative": query + " 对比 ablation",
            }
        else:
            variants = {
                "refined": query + " paper explained",
                "critique": query + " limitations criticism",
                "academic": query + " survey benchmark",
                "primary": query,
                "comparative": query + " comparison ablation",
            }
    elif kind == "movies":
        if is_chinese:
            variants = {
                "refined": query + " 影评 解读",
                "critique": query + " 争议 不足 批评",
                "academic": query + " 主题 隐喻 价值观",
                "primary": query,
                "comparative": query + " 系列 续集 对比",
            }
        else:
            variants = {
                "refined": query + " review analysis",
                "critique": query + " controversy criticism",
                "academic": query + " themes metaphor values",
                "primary": query,
                "comparative": query + " series sequel comparison",
            }
    else:
        variants = {
            "refined": query + (" 详解" if is_chinese else " explained in detail"),
            "critique": query + (" 局限 争议" if is_chinese else " limitations criticism"),
            "academic": query + (" 综述 演进" if is_chinese else " survey review"),
            "primary": query,
            "comparative": query + (" 对比" if is_chinese else " comparison"),
        }

    return ResearchPlan(
        query=query,
        kind=kind,
        depth="normal",
        language_priority="zh" if is_chinese else "en",
        english_search_terms=english_terms,
        variants=variants,
        target_sites=target_sites,
        relevance_threshold=0.40,
        rationale=(
            f"Auto-generated plan. kind={kind} "
            f"language_priority={'zh-primary' if is_chinese else 'en-primary'} "
            f"target_sites={list(target_sites) or 'none'} "
            f"english_baseline={'yes' if english_terms else 'no'}."
        ),
    )


# ---------------------------------------------------------------------------
# Search matrix (one row = one parallel task)
# ---------------------------------------------------------------------------
def build_search_matrix_from_plan(
    plan: ResearchPlan,
    *,
    config: Config,
) -> tuple[list[MatrixRow], list[str]]:
    """Build the search matrix from an LLM-supplied ResearchPlan.

    Plan-driven path. No substring matching, no dictionaries.

    **rebalance rule**:

        When ``len(plan.variants) + len(plan.english_search_terms) + n_site``
        exceeds ``max_q``, candidates are appended in priority order and
        sliced at the cap. Priority order is:

            1. **Site-targeted** (primary source value — always first)
            2. **中文原始** (always if zh)
            3. **英文基础** (always if english available — cross-language contrast)
            4. **Chinese variants** (in declared order; LAST variant dropped
               first when cap tight)
            5. **English variants** (en_variant / en_academic; dropped if no room)
            6. **P2-反方视角** (universal supplementary)

        This guarantees: even when plan is "variant-heavy" (5+ variants +
        3 english + many sites), the English baseline survives because it's
        priority 3. Excess variants are truncated from the END (comparative
        is the least actionable, so dropping it first).

    Args:
        plan: LLM-supplied research plan.
        config: resolved configuration (provides topk + max_queries caps).

    Returns:
        Tuple of ``(rows, dropped_descriptions)``:
            * ``rows``: list of :class:`MatrixRow` (length ≤ ``max_queries_for()``)
            * ``dropped_descriptions``: list of human-readable strings
              describing tasks that didn't fit in the cap (for dry-run preview
              + report transparency)
    """
    topk = config.topk_for()
    max_q = config.max_queries_for()
    default_exclude = list(LOWQ_DOMAINS) + [
        "csdn.net", "baike.baidu.com", "sohu.com",
    ]

    # Build candidates in priority order. Slice at cap; report drops.
    candidates: list[tuple[str, MatrixRow]] = []  # (group_label, row)

    # Site-targeted tasks use the **English baseline
    # query** (plan.english_search_terms[0]) when available, falling back
    # to plan.query for zh-only / no-english-plans. Reason: most English-
    # first target sites (lmsys.org, arxiv.org, github.com,
    # huggingface.co) are NOT indexed for Chinese content — running
    # ``site:lmsys.org 大模型 天梯榜 2026`` returns 0 results on every
    # search engine we tested. Using the English baseline query restores
    # coverage on these primary-source sites.
    #
    # per-site English term
    # selection. Instead of always using english_search_terms[0] for
    # every site, pick the english term most relevant to that specific
    # site (substring-match heuristic, no hardcoded alias table — see
    # ``pick_site_query`` in :mod:`deep_dive.query_classifier`).
    # Example: ``site:huggingface.co`` now gets the "Hugging Face …"
    # english_search_terms entry instead of the generic "LLM leaderboard"
    # baseline, recovering 80%+ of cases that were previously wasted.
    #
    # Note: zh-only plans (or plans with no english_search_terms) skip
    # English routing entirely and fall back to plan.query. This preserves
    # the contract: a Chinese query on a Chinese-only plan must
    # keep using the Chinese query, never be silently upgraded to English.
    _use_english_for_sites = bool(plan.english_search_terms) and plan.language_priority != "zh-only"
    _site_fallback = (
        plan.english_search_terms[0] if _use_english_for_sites else plan.query
    )

    # ---- 1. Site-targeted (always first — primary source value) ---------
    n_site = 2 if config.depth == "normal" else (3 if config.depth == "full" else 0)
    for site in plan.target_sites[:n_site]:
        site_q = pick_site_query(
            site,
            plan.english_search_terms if _use_english_for_sites else (),
            fallback=_site_fallback,
        )
        candidates.append((
            f"站点定向:{site}",
            MatrixRow(
                note=f"站点定向:{site}",
                query=f"{site_q} site:{site}",
                topk=12,
                exclude=tuple(default_exclude),
            ),
        ))

    # ---- 2. Chinese original (always if zh) -----------------------------
    if plan.language_priority != "en-only":
        candidates.append((
            "中文原始",
            MatrixRow(
                note="中文原始",
                query=plan.query,
                topk=topk,
                exclude=tuple(default_exclude),
            ),
        ))

    # ---- 3. English baseline (cross-language contrast, ALWAYS reserved) -
    # this slot is reserved BEFORE Chinese variants so it survives
    # cap-tight scenarios. If english_search_terms is empty OR
    # language_priority is "zh-only", skip this slot.
    has_english = (
        bool(plan.english_search_terms)
        and plan.language_priority != "zh-only"
    )
    if has_english:
        candidates.append((
            "英文基础",
            MatrixRow(
                note="英文基础",
                query=plan.english_search_terms[0],
                topk=topk,
                exclude=tuple(default_exclude),
            ),
        ))

    # ---- 4. Chinese variants (plan.variants, in declared order) ---------
    # each variant's query string may use ``||`` as a
    # parallel-coverage separator. This lets the LLM (or hand-written
    # plan) emit up to N independent queries for a single variant key,
    # so the matrix gets N parallel rows for that slot. Example:
    #     "refined": "智谱 GLM-5.3 Flash 性能 价格||智谱 GLM-5.3 Flash 评测 上下文||..."
    # yields 4 independent "中文细化" tasks instead of 1, dramatically
    # improving recall for fresh-news / niche queries where the canonical
    # query may not match the corpus.
    if plan.language_priority != "en-only":
        for variant_key in ("refined", "critique", "academic", "primary", "comparative"):
            raw = plan.variants.get(variant_key, "").strip()
            if not raw:
                continue
            # Split on ``||`` and de-dup; keep insertion order.
            parts = [p.strip() for p in raw.split("||")]
            parts = [p for p in parts if p]
            seen_part: set[str] = set()
            unique_parts: list[str] = []
            for p in parts:
                if p == plan.query or p in seen_part:
                    continue
                seen_part.add(p)
                unique_parts.append(p)
            if not unique_parts:
                continue
            note = variant_note_label(variant_key)
            for sub_q in unique_parts:
                candidates.append((
                    f"variant:{variant_key}",
                    MatrixRow(
                        note=note,
                        query=sub_q,
                        topk=max(2, topk - 5),
                        exclude=tuple(default_exclude),
                    ),
                ))

    # ---- 5. English variants (en_variant, en_academic) ------------------
    if has_english and len(plan.english_search_terms) >= 2:
        for i, term in enumerate(plan.english_search_terms[1:3], start=1):
            if i == 1:
                note = "英文案例"
                this_topk = max(2, topk - 3)
            elif i == 2:
                note = "英文学术"
                this_topk = max(2, topk - 5)
            else:
                note = f"英文补充{i + 1}"
                this_topk = max(2, topk - 5)
            candidates.append((
                f"en_variant:{i}",
                MatrixRow(
                    note=note,
                    query=term,
                    topk=this_topk,
                    exclude=tuple(default_exclude),
                ),
            ))

    # ---- 6. Universal supplementary (P2 critique) -----------------------
    if config.depth in ("normal", "full"):
        critique_q = plan.variants.get(
            "critique",
            f"{plan.query} 争议 批评 局限性 反对意见",
        )
        candidates.append((
            "P2-反方视角",
            MatrixRow(
                note="P2-反方视角",
                query=critique_q,
                topk=max(2, topk - 8),
                exclude=tuple(default_exclude),
            ),
        ))

    # Slice to cap, surface drops
    kept = candidates[:max_q]
    dropped = candidates[max_q:]
    return [row for _, row in kept], [label for label, _ in dropped]


# ---------------------------------------------------------------------------
# Single-task runner
# ---------------------------------------------------------------------------

def _slug_dir_name(query: str) -> str:
    """Filesystem-safe directory name from a query (mirror legacy behavior)."""
    import re
    return re.sub(r'[\\/*?:"<>|]', "_", query).strip()[:80] or "search"


def _run_one_task(
    row: MatrixRow,
    *,
    base_dir: Path,
    engines: dict[str, SearchEngine],
    config: Config,
    cookies_map: dict,
    main_query: str,
    fetcher_classes: dict[str, type[Fetcher]] | None = None,
) -> TaskResult:
    """Execute one matrix row with **mmx → Tavily fallback chain**.

    Args:
        row: the matrix row to run.
        base_dir: the ``raw/`` directory inside the topic dir.
        engines: mapping ``{"mmx": MMXEngine, "tavily": TavilyEngine}``.
        config: resolved configuration.
        cookies_map: loaded cookies mapping.
        main_query: the user's original (top-level) query — used for
            the relevance two-stage check on sub-queries.
        fetcher_classes: optional ``{"primary": cls, "fallback": cls}``
            mapping. Defaults to the real Playwright/CloudScraper
            fetchers. Tests inject mocks via this hook.

    Returns:
        A :class:`TaskResult` summarizing the outcome. Always has a
        non-null ``output_dir`` so debug artifacts survive even on
        failure.

    Notes:
        **Fallback chain**: in ``auto`` mode (default), if mmx returns
        fewer URLs than ``topk`` (e.g. niche query, partial coverage),
        Tavily is called with the remainder to fill the gap. This
        restores the legacy ``search_urls()`` behaviour that was lost
        when we split engines into separate ``SearchEngine`` classes.

        **Early ``mkdir``**: ``task_dir`` is created BEFORE any engine
        call so debug artifacts are persisted even when the engine
        raises an exception.
    """
    # Resolve fetchers (allow tests to inject mocks).
    fc = fetcher_classes or {"primary": PlaywrightFetcher, "fallback": CloudScraperFetcher}
    PrimaryFetcher = fc["primary"]
    FallbackFetcher = fc["fallback"]

    # Create task_dir BEFORE any engine call so debug artifacts are
    # always persisted (even when the engine raises or returns 0 hits).
    task_dir = base_dir
    task_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()

    note_lower = row.note.lower()
    site_targeted = note_lower.startswith("站点定向")

    # site-targeted tasks prefer **Tavily as primary**
    # because Tavily honors the ``site:`` operator natively, while MMX
    # silently ignores it and returns generic hits — every one of which
    # then gets dropped by the P1 site-targeted post-filter.
    #
    # User-reported incident (2026-08-29): a run on "<query-tech>"
    # with ``site:arxiv.org`` returned 0 URLs because MMX gave back 9
    # off-domain hits that all failed the post-filter, and the engine
    # fallback chain never got a chance to try Tavily (the old code had
    # ``use_mmx_only = ... or site_targeted`` which hard-coded site-
    # targeted to MMX).
    #
    # Only kick in when:
    #   - Tavily is actually available (``engines.get("tavily")`` is not None)
    #   - User did NOT explicitly disable it (``--no-tavily``)
    #   - User did NOT explicitly choose ``--search-engine mmx``
    # Otherwise we preserve the original user intent and behaviour.
    prefer_tavily_for_site = (
        site_targeted
        and engines.get("tavily") is not None
        and not config.no_tavily
        and config.search_engine != "mmx"
    )

    # Engine selection ().
    #
    # Precedence:
    #   1. ``--search-engine tavily``   → Tavily only, no fallback
    #   2. ``--search-engine mmx`` or ``--no-tavily`` → MMX only, no fallback
    #   3. auto + site-targeted         → Tavily primary, MMX fallback
    #   4. auto + non-site-targeted     → MMX primary, Tavily fallback (legacy)
    if config.search_engine == "tavily":
        primary_engine = engines.get("tavily")
        fallback_engine = None  # user explicitly chose Tavily-only
    elif config.search_engine == "mmx" or config.no_tavily:
        primary_engine = engines.get("mmx")
        fallback_engine = None
    elif prefer_tavily_for_site:
        primary_engine = engines.get("tavily")
        fallback_engine = engines.get("mmx")
    else:  # auto (default), non-site-targeted
        # mmx is the preferred primary; Tavily is the fallback when mmx
        # returns too few hits. If mmx is unavailable, fall through to
        # Tavily as primary so the run still gets *something*.
        if engines.get("mmx"):
            primary_engine = engines["mmx"]
            fallback_engine = engines.get("tavily")
        else:
            primary_engine = engines.get("tavily")
            fallback_engine = None

    # Build search query with -site: excludes
    exclude_query = row.query
    if row.exclude:
        exclude_query += " " + " ".join(f"-site:{d}" for d in row.exclude)

    # 1. Primary search
    hits: list[SearchHit] = []
    primary_status = "ok"
    primary_error: str | None = None
    primary_audit: dict[str, Any] = {"key_used": None, "keys_tried": [], "keys_exhausted": []}
    if primary_engine is None:
        primary_status = "no_engine"
        primary_error = "no engine available for this task"
    else:
        try:
            hits = primary_engine.search(exclude_query, row.topk)
        except SearchEngineQuotaError:
            primary_status = "quota"
        except SearchEngineError as e:
            primary_status = "failed"
            primary_error = str(e)[:200]
        primary_audit = _capture_engine_audit(primary_engine)

    # Initialise fallback_status BEFORE the 1b degradation step so the
    # degradation block can record its outcome (ok/quota/failed) without
    # hitting a NameError, and so step 2's unconditional reset doesn't
    # clobber it. The legacy single-step fallback chain used to set this
    # here, but split the logic into two passes that both want
    # to write to it.
    fallback_status = "skipped"

    # 1b. Engine degradation.
    # When primary hits QUOTA, promote fallback_engine to primary and
    # retry. Rationale: MMX and Tavily have **independent quota
    # systems** — a mmx-quota exhaustion says nothing about Tavily
    # availability. The legacy "skip Tavily on quota" rule (preserved
    # in the fallback chain below) was a relic of the old monolithic
    # engine layer.
    # Exemptions:
    #   - ``config.search_engine != "auto"`` → user explicitly chose an engine
    #   - ``config.no_tavily``              → user disabled Tavily
    #   - ``fallback_engine is None`` or same as primary → nothing to degrade to
    #
    # the previous ``not use_mmx_only`` gate incorrectly
    # prevented degradation for site-targeted tasks (which had
    # ``use_mmx_only=True`` in  because the original logic hard-
    # coded site-targeted to MMX). After the engine-selection
    # rewrite, site-targeted tasks in auto mode legitimately have
    # fallback_engine=MMX, so degradation to MMX on Tavily-quota should
    # fire just like any other auto-mode task.
    can_degrade = config.search_engine == "auto" and not config.no_tavily
    degraded_to: str | None = None
    if (
        primary_status == "quota"
        and can_degrade
        and fallback_engine is not None
        and fallback_engine is not primary_engine
    ):
        fb_name = _engine_name(fallback_engine)
        try:
            hits = fallback_engine.search(exclude_query, row.topk)
            degraded_to = fb_name
            primary_status = "ok"
            primary_error = None
            fallback_status = "ok"     # degradation recovered the task
        except SearchEngineQuotaError:
            # Both engines quota-exhausted: degrade_to records this so the
            # audit log shows the secondary attempt also failed.
            degraded_to = fb_name
            fallback_status = "quota"
        except SearchEngineError as e:
            degraded_to = fb_name
            primary_status = "failed"
            primary_error = f"degraded to {fb_name}: {e}"[:200]
            fallback_status = "failed"
        # Capture fallback-engine credential audit (used for fallback
        # rotation when degradation happened).
        fallback_audit = _capture_engine_audit(fallback_engine)

    # 2. Fallback chain (mmx → Tavily) — only if primary returned insufficient.
    # Skipped when degraded_to is set: we already used fallback_engine as the
    # primary, so re-calling it would be a wasteful double-spend.
    # Note: fallback_status was set above ("skipped" or whatever 1b wrote);
    # we only overwrite it if we actually run this block.
    fallback_used = bool(degraded_to)
    fallback_audit = fallback_audit if "fallback_audit" in locals() else {"key_used": None, "keys_tried": [], "keys_exhausted": []}
    if (
        primary_status != "quota"
        and fallback_engine is not None
        and fallback_engine is not primary_engine
        and degraded_to is None  # skip if already used for degradation
        and len(hits) < row.topk              # got partial coverage; try to fill
    ):
        # Request 2x the gap so smart_filter / dedup doesn't leave us short
        need = max((row.topk - len(hits)) * 2, row.topk)
        fallback_used = True  # we attempted the fallback path; result may still be ok/failed/quota
        try:
            extra = fallback_engine.search(exclude_query, need)
            seen = {h.url for h in hits}
            for h in extra:
                if h.url not in seen:
                    hits.append(h)
                    seen.add(h.url)
                    if len(hits) >= row.topk:
                        break
            fallback_status = "ok"
        except SearchEngineQuotaError:
            fallback_status = "quota"
        except SearchEngineError:
            fallback_status = "failed"
        # Capture fallback-engine credential audit (used for the partial-
        # coverage rotation path).
        fallback_audit = _capture_engine_audit(fallback_engine)

    # 1c. Ultimate fallback (fix): DuckDuckGo as the last-resort
    # engine so quota exhaustion of MMX+Tavily never causes a task to
    # return 0 URLs. DDG has no API key and no per-IP quota, so we
    # always try it when both primary and (legacy) fallback returned
    # nothing usable. This is the user's invariant: "配额超了就应该
    # fallback 到下一个优先级的引擎/KEY，不应该影响搜索质量。"
    ultimate_engine = engines.get("duckduckgo")
    ultimate_used = False
    ultimate_status: str | None = None
    if (
        ultimate_engine is not None
        and ultimate_engine is not primary_engine
        and ultimate_engine is not fallback_engine
        and not hits
        and primary_status in ("quota", "failed", "no_engine")
    ):
        ultimate_used = True
        try:
            ddg_hits = ultimate_engine.search(exclude_query, row.topk)
            if ddg_hits:
                hits = ddg_hits[: row.topk]
                ultimate_status = "ok"
                # Recover the task: mark status ok and record degradation chain.
                primary_status = "ok"
                primary_error = None
                prev = degraded_to or _engine_name(primary_engine)
                degraded_to = f"{prev}->{ultimate_engine.name}"
                fallback_status = fallback_status if fallback_status not in ("skipped", None) else "ok"
            else:
                ultimate_status = "empty"
        except SearchEngineError as e:
            ultimate_status = f"failed:{type(e).__name__}"

    # 2c. Site-targeted fallback ()
    # When the site-targeted task returns 0 hits from mmx, retry once with
    # the site: prefix stripped. Rationale: some English-first sites have
    # indexed content but ``site:domain + 中文 query`` returns 0 results
    # even when English-language pages exist (e.g. ``site:lmsys.org 大模型
    # 天梯榜 2026`` → 0; same query without ``site:`` recovers English hits).
    # Costs ~1 s because we re-use the same engine.
    site_fallback_used = False
    site_fallback_status: str | None = None
    if (
        site_targeted
        and primary_status == "ok"
        and not hits
        and primary_engine is not None
        and " site:" in row.query
    ):
        site_fallback_used = True
        _fallback_q = row.query.split(" site:", 1)[0].strip()
        _fb_exclude = _fallback_q
        if row.exclude:
            _fb_exclude += " " + " ".join(f"-site:{d}" for d in row.exclude)
        try:
            hits = primary_engine.search(_fb_exclude, row.topk)
            site_fallback_status = "ok" if hits else "still_empty"
        except SearchEngineError as e:
            site_fallback_status = f"failed:{type(e).__name__}"

    # 2b. Site-targeted post-filter (P1 fix)
    # Some sites (e.g. stackoverflow.com) have no content for China-native
    # concepts. The ``site:`` filter in the query is best-effort; engines
    # sometimes return off-site noise. If **all** hits are off-site, mark
    # as NO_RESULTS so the user can see why the task yielded nothing.
    site_filtered_out = False
    if site_targeted and hits and primary_status == "ok":
        target_site = row.note.split(":", 1)[1].strip().lower()
        pre_filter_count = len(hits)
        kept = [h for h in hits if target_site in h.url.lower()]
        if not kept:
            # Every hit was off-site — surface this as a clean no-result.
            return TaskResult(
                note=row.note, query=row.query, status=TaskStatus.NO_RESULTS,
                output_dir=task_dir, duration_seconds=time.time() - start,
                extra={
                    "engine": _engine_name(primary_engine),
                    "fallback_used": fallback_used,
                    "fallback_status": fallback_status if fallback_used else None,
                    "degraded_to": degraded_to,
                    "site_filtered_out": True,
                    "target_site": target_site,
                    "raw_hit_count": pre_filter_count,
                },
            )
        if len(kept) < pre_filter_count:
            hits = kept
            site_filtered_out = True

    # 3. Status determination (always include output_dir for debugging)
    base_extra = {
        "engine": _engine_name(primary_engine),
        "fallback_used": fallback_used,
        "fallback_status": fallback_status if fallback_used else None,
        "degraded_to": degraded_to,
        "site_filtered_out": site_filtered_out,
        # Ultimate (DuckDuckGo) fallback audit so users can
        # see when the rescue engine actually fired.
        "ultimate_used": ultimate_used,
        "ultimate_status": ultimate_status,
        # Per-engine credential rotation audit so users can
        # verify the N-key fallback actually tried all configured keys.
        "primary_key_used": primary_audit.get("key_used"),
        "primary_keys_tried": primary_audit.get("keys_tried", []),
        "primary_keys_exhausted": primary_audit.get("keys_exhausted", []),
        "fallback_key_used": fallback_audit.get("key_used"),
        "fallback_keys_tried": fallback_audit.get("keys_tried", []),
        "fallback_keys_exhausted": fallback_audit.get("keys_exhausted", []),
    }
    if primary_status == "quota":
        return TaskResult(
            note=row.note, query=row.query, status=TaskStatus.QUOTA_EXCEEDED,
            output_dir=task_dir, duration_seconds=time.time() - start,
            error=primary_error, extra=base_extra,
        )
    if primary_status == "failed":
        return TaskResult(
            note=row.note, query=row.query, status=TaskStatus.FAILED,
            output_dir=task_dir, duration_seconds=time.time() - start,
            error=primary_error, extra=base_extra,
        )
    if not hits:
        return TaskResult(
            note=row.note, query=row.query, status=TaskStatus.NO_RESULTS,
            output_dir=task_dir, duration_seconds=time.time() - start,
            extra=base_extra,
        )

    # 4. Fetch + extract + relevance
    pipeline_cfg = PipelineConfig(
        output_dir=task_dir,
        main_query=main_query,
        page_timeout_s=config.task_timeout_s,
        min_chars_for_quality=config.min_chars,
        enable_relevance_check=True,
    )
    pipeline = CrawlPipeline(
        pipeline_cfg,
        primary_fetcher=PrimaryFetcher(timeout_s=config.task_timeout_s),
        fallback_fetcher=FallbackFetcher(timeout_s=config.task_timeout_s),
        cookies_map=cookies_map,
    )
    try:
        fetches = pipeline.run(
            [h.url for h in hits],
            source_task=row.note,
            query_index=-1,
        )
    except Exception as e:
        return TaskResult(
            note=row.note, query=row.query, status=TaskStatus.FAILED,
            output_dir=task_dir, duration_seconds=time.time() - start,
            error=f"pipeline: {type(e).__name__}: {e}"[:200],
            extra={**base_extra, "n_attempts": len(hits)},
        )

    n_success = sum(1 for f in fetches if f.status == FetchStatus.SUCCESS)
    duration = time.time() - start
    if n_success == 0:
        return TaskResult(
            note=row.note, query=row.query, status=TaskStatus.NO_RESULTS,
            output_dir=task_dir, duration_seconds=duration,
            extra={**base_extra, "n_attempted": len(fetches)},
        )
    return TaskResult(
        note=row.note, query=row.query, status=TaskStatus.SUCCESS,
        output_dir=task_dir, duration_seconds=duration,
        url_count=n_success,
        extra={**base_extra, "n_attempted": len(fetches)},
    )


def _engine_name(engine) -> str:
    """Return an engine's canonical name (or ``"none"`` for None).

    Used in :class:`TaskResult` extras for post-mortem diagnostics.
    """
    if engine is None:
        return "none"
    return getattr(engine, "name", type(engine).__name__)


def _capture_engine_audit(engine) -> dict[str, Any]:
    """Snapshot a MultiKeyEngine's per-call rotation audit.

    Non-multi engines (e.g. mocks in tests) return an empty audit dict
    so the orchestrator can record the same fields unconditionally.
    """
    if engine is None:
        return {"key_used": None, "keys_tried": [], "keys_exhausted": []}
    getter = getattr(engine, "get_audit", None)
    if getter is None:
        return {"key_used": None, "keys_tried": [], "keys_exhausted": []}
    return getter()


def _pick_engine(label: str, engines: dict[str, SearchEngine]) -> SearchEngine:
    """Resolve an engine label to a ready engine instance.

    .. deprecated::
        Inlined into :func:`_run_one_task` after the fallback-chain
        rewrite. Kept for backwards compatibility with external
        callers and the orchestrator's unit tests.
    """
    label = (label or "auto").lower()
    if label in ("mmx", "auto"):
        if "mmx" in engines:
            return engines["mmx"]
    if label in ("tavily", "auto"):
        if "tavily" in engines:
            return engines["tavily"]
    # Fallback: any available engine
    for e in engines.values():
        return e
    raise SearchEngineError("No search engines available")


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:20] or "x"


# ---------------------------------------------------------------------------
# Parallel dispatch with heartbeat
# ---------------------------------------------------------------------------

class Orchestrator:
    """One-stop driver for a full deep-dive run.

    Usage::

        cfg = load_config()
        orch = Orchestrator(cfg)
        result = orch.run(query="<query>")
        # result.task_results, result.aggregated, result.report_path

    The orchestrator is the only object that knows about engines +
    fetchers + matrix + parallel dispatch. Sub-agents that want to
    customize one piece can replace the appropriate ``Orchestrator``
    attribute (e.g. ``orch.engines["mmx"] = MyMMXEngine()``) before
    calling :meth:`run`.
    """

    def __init__(
        self,
        config: Config,
        *,
        engines: dict[str, SearchEngine] | None = None,
        fetchers: dict[str, type[Fetcher]] | None = None,
        heartbeat: Callable[[str], None] | None = None,
    ) -> None:
        """Create an Orchestrator.

        Args:
            config: resolved configuration.
            engines: optional dict ``{"mmx": MMXEngine(), "tavily": ...}``.
                Defaults to :meth:`_default_engines`.
            fetchers: optional dict ``{"primary": PlaywrightFetcher,
                "fallback": CloudScraperFetcher}``. Values are **classes**
                (not instances) — the orchestrator instantiates them per-task.
                Defaults to the real Playwright/CloudScraper fetchers.
            heartbeat: optional callback for status messages.
        """
        self.config = config
        self.engines: dict[str, SearchEngine] = engines or self._default_engines(config)
        self._fetcher_classes: dict[str, type[Fetcher]] = fetchers or {
            "primary": PlaywrightFetcher,
            "fallback": CloudScraperFetcher,
        }
        self.heartbeat = heartbeat or (lambda msg: safe_print(msg))

    # ------------------------------------------------------------------
    # Engine defaults
    # ------------------------------------------------------------------

    @staticmethod
    def _default_engines(config: Config) -> dict[str, SearchEngine]:
        out: dict[str, SearchEngine] = {}
        try:
            # Pass mmx_invocations to allow configuring multiple
            # MMX profiles (different / accounts / env vars / extra args).
            # Empty list → default single-invocation pool (behaviour).
            out["mmx"] = MMXEngine(
                invocations=config.mmx_invocations or None,
                timeout_s=DEFAULT_TASK_TIMEOUT_S,
            )
        except Exception as e:
            safe_print(f"{TAG_WARN} MMXEngine init failed: {e}")
        try:
            # TavilyEngine auto-detects keys from explicit
            # config (tavily_keys / api_key / api_key_backup) AND from
            # env vars (TAVILY_API_KEYS / TAVILY_API_KEY_BACKUP /
            # TAVILY_API_KEY). We always create the engine so its pool
            # reflects whatever the user configured; the engine itself
            # will surface "no credentials" as SearchEngineError on
            # search() if there's truly nothing to use.
            tavily_keys = list(config.tavily_keys) or None
            out["tavily"] = TavilyEngine(
                keys=tavily_keys,
                api_key=config.tavily_api_key,
                api_key_backup=config.tavily_api_key_backup,
                timeout_s=DEFAULT_TASK_TIMEOUT_S,
            )
        except Exception as e:
            safe_print(f"{TAG_WARN} TavilyEngine init failed: {e}")
        # Always register DuckDuckGo as the **ultimate fallback**
        # so quota exhaustion of MMX+Tavily never causes a task to return
        # 0 URLs. DDG requires no API key and has no per-IP quota.
        try:
            out["duckduckgo"] = DuckDuckGoEngine(
                timeout_s=DEFAULT_TASK_TIMEOUT_S,
            )
        except Exception as e:
            safe_print(f"{TAG_WARN} DuckDuckGoEngine init failed: {e}")
        # P2 #9 fix: log which engines are actually available so the user
        # can verify their environment at a glance.
        available = ", ".join(sorted(out.keys())) or "(none!)"
        # Probe the actual mmx executable path (if any) for the log.
        from deep_dive.crawler.engines.mmx import _resolve_mmx_path as _mmx_which
        mmx_path = _mmx_which() if "mmx" in out else None
        mmx_str = f" ({mmx_path})" if mmx_path else ""
        # Surface configured credential counts so the user can
        # verify multi-key setup at a glance.
        cred_strs = []
        if "tavily" in out:
            cred_strs.append(f"tavily={out['tavily'].pool.total_count} key(s)")
        if "mmx" in out:
            cred_strs.append(f"mmx={out['mmx'].pool.total_count} invocation(s)")
        cred_summary = f" [{', '.join(cred_strs)}]" if cred_strs else ""
        safe_print(f"[ENGINE] available: {available}{mmx_str}{cred_summary}")
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        query: str,
        *,
        run_id: str | None = None,
        plan: ResearchPlan | None = None,
    ) -> CrawlResult:
        """Execute one full deep-dive run for ``query``.

        Args:
            query: the user search query.
            run_id: optional explicit run identifier. If absent, an
                ASCII slug of the query + a timestamp is used.
            plan: optional LLM-supplied :class:`ResearchPlan`. When
                provided, deep-dive consumes ``plan.variants``,
                ``plan.english_search_terms`` and ``plan.target_sites``
                instead of falling back to the hardcoded
                 matchers. When ``None``,
                a warning is printed and legacy behaviour is used.

        Returns:
            A :class:`CrawlResult` summarising the run. Also persists
            ``report.md``, ``summary.json``, and (if applicable) the
            auto-rescue file. ``summary.json`` includes the plan (if any)
            so the run is fully reproducible.
        """
        from deep_dive.crawler.cookies import load_cookies

        config = self.config
        query = (query or "").strip()
        if not query:
            raise ValueError("query is required")

        # Run-id + topic-dir setup
        from datetime import datetime
        slug = _ascii_slug(query)[:40] or "search"
        suffix = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = _ascii_slug(suffix)[:40] or "default"
        topic_dir = Path(config.output_dir) / f"{slug}__{suffix}"
        raw_dir = topic_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        debug_dir = topic_dir / "debug" if config.debug else None
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)

        safe_print("\n" + "=" * 70)
        safe_print(f"  deep-dive | query='{query}' depth={config.depth}")
        safe_print("=" * 70)

        # Cookies
        cookies_map = load_cookies()
        if cookies_map:
            from deep_dive.crawler.cookies import count_loaded
            n, s = count_loaded(cookies_map)
            safe_print(f"[COOKIE] loaded {n} cookies across {s} sites")
        else:
            safe_print("[COOKIE] no cookies.json found (optional)")

        # Variants + matrix (plan-driven)
        dropped_tasks: list[str] = []
        if plan is None:
            plan = auto_plan(query)
            safe_print(
                "[INFO] No plan supplied; auto-generated a minimal plan from the query. "
                "Pass an explicit plan for richer variants / target_sites."
            )
        variants = generate_variants_from_plan(plan)
        matrix, dropped_tasks = build_search_matrix_from_plan(plan, config=config)
        mode = "plan"
        safe_print(
            f"[MATRIX] {len(matrix)} tasks | concurrency={config.max_workers} | mode={mode}"
        )
        if dropped_tasks:
            safe_print(
                f"[MATRIX] {len(dropped_tasks)} tasks dropped due to cap={config.max_queries_for()}:"
            )
            for label in dropped_tasks:
                safe_print(f"  - dropped: {label}")
        for i, row in enumerate(matrix, 1):
            safe_print(f"  {i}. [{row.note}] topk={row.topk}")

        if debug_dir:
            try:
                (debug_dir / "matrix.json").write_text(
                    json.dumps(
                        {
                            "query": query,
                            "depth": config.depth,
                            "variants": variants,
                            "matrix": [
                                {
                                    "note": r.note,
                                    "query": r.query,
                                    "topk": r.topk,
                                    "exclude": list(r.exclude),
                                }
                                for r in matrix
                            ],
                        },
                        ensure_ascii=False, indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception as e:
                safe_print(f"{TAG_WARN} matrix.json save failed: {e}")

        # Parallel dispatch
        task_results = self._dispatch_parallel(
            matrix, base_dir=raw_dir, cookies_map=cookies_map, main_query=query,
        )

        # Aggregate
        aggregator = Aggregator()
        aggregated = aggregator.aggregate(task_results, raw_dir)

        # Auto-rescue (raw_all.txt)
        try:
            auto_rescue_raw(topic_dir=topic_dir, raw_dir=raw_dir, aggregated=aggregated)
        except Exception as e:
            safe_print(f"[RESCUE-ERR] {type(e).__name__}: {e}")

        # Global status decision
        n_quota = sum(1 for r in task_results if r.status == TaskStatus.QUOTA_EXCEEDED)
        n_empty = sum(1 for r in task_results if r.status == TaskStatus.NO_RESULTS)
        n_success = sum(1 for r in task_results if r.status == TaskStatus.SUCCESS)
        n_total = len(task_results)
        if n_quota > n_total / 2:
            global_status = "quota_exceeded"
        elif n_empty == n_total:
            global_status = "no_results"
        elif n_success != n_total:
            global_status = "mixed"
        else:
            global_status = "success"

        # summary.json
        summary_file = topic_dir / "summary.json"
        summary = {
            "query": query,
            "depth": config.depth,
            "lang": config.lang,
            "matrix_count": len(matrix),
            "task_results": [_task_to_json(r) for r in task_results],
            "aggregated_summary": {
                "total_urls": aggregated.total_urls,
                "global_status": global_status,
            },
            "timestamp": datetime.now().isoformat(),
            "config": config.to_dict(redact_secrets=True),
            # Persist the plan (or absence) so the run is fully
            # reproducible + auditable.
            "plan_used": plan is not None,
            "plan": plan.to_dict() if plan is not None else None,
            "matrix_mode": mode,
        }
        try:
            summary_file.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            safe_print(f"[SUMMARY-ERR] write failed: {e}")

        report_path: Path | None = None
        try:
            query_kind_for_report = kind_from_plan(plan).value
            report_path = build_report(
                query=query,
                query_kind=query_kind_for_report,
                depth=config.depth,
                lang=config.lang,
                matrix=matrix,
                task_results=task_results,
                aggregated=aggregated,
                output_dir=topic_dir,
                min_chars=config.min_chars,
                global_status=global_status,
                # Report renders the LLM plan + drops
                # + matrix_mode so the reader can see what the run was
                # actually trying to do (not just what it landed on).
                plan=plan,
                matrix_mode=mode,
                dropped_tasks=dropped_tasks,
            )
        except Exception as e:
            safe_print(f"{TAG_ERR} report build failed: {e}")

        # Capy summary
        if report_path and report_path.exists():
            try:
                append_capy_section(
                    report_path=report_path, query=query,
                    task_results=task_results,
                    aggregated_meta=_all_meta(aggregated),
                )
            except Exception as e:
                safe_print(f"{TAG_WARN} capy summary failed: {e}")

        safe_print("\n" + "=" * 70)
        safe_print(f"{TAG_DONE} all complete!")
        safe_print(f"  unique URLs: {aggregated.total_urls}")
        safe_print(f"  report: {report_path or '(none)'}")
        safe_print("=" * 70)

        return CrawlResult(
            topic=query,
            run_id=suffix,
            task_results=tuple(task_results),
            aggregated=aggregated,
            report_path=report_path,
            global_status=global_status,
        )

    # ------------------------------------------------------------------
    # Parallel dispatch (with heartbeat + global watchdog)
    # ------------------------------------------------------------------

    def _dispatch_parallel(
        self,
        matrix: list[MatrixRow],
        *,
        base_dir: Path,
        cookies_map: dict,
        main_query: str,
    ) -> list[TaskResult]:
        workers = max(1, self.config.max_workers)
        results: list[TaskResult] = []
        done_count = [0]
        done_lock = threading.Lock()
        start = time.time()

        def _inc() -> int:
            with done_lock:
                done_count[0] += 1
                return done_count[0]

        def _get() -> int:
            with done_lock:
                return done_count[0]

        # Heartbeat debouncing: suppress ``[HEARTBEAT] N/M done`` when
        # a real event (task start, [OK], [WARN]) fired within the
        # last ``DEFAULT_HEARTBEAT_INTERVAL_S`` seconds. Without this
        # the loop spams "0/6 done" every interval while tasks are
        # silently making progress, drowning the log. Updated by
        # ``_mark_event`` after every meaningful log line.
        event_lock = threading.Lock()
        last_event_ts = [time.time()]

        def _mark_event() -> None:
            with event_lock:
                last_event_ts[0] = time.time()

        # Heartbeat thread
        stop_event = threading.Event()
        def heartbeat_loop():
            while not stop_event.is_set():
                stop_event.wait(DEFAULT_HEARTBEAT_INTERVAL_S)
                if stop_event.is_set():
                    break
                with event_lock:
                    # Skip if a real event fired recently — user already
                    # saw something fresh, repeating "N/M done" is noise.
                    if time.time() - last_event_ts[0] < DEFAULT_HEARTBEAT_INTERVAL_S:
                        continue
                self.heartbeat(f"{TAG_HEARTBEAT} {_get()}/{len(matrix)} done")
                _mark_event()

        hb = threading.Thread(target=heartbeat_loop, daemon=True)
        hb.start()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = []
            for i, row in enumerate(matrix):
                task_out = base_dir / f"task_{i:02d}_{_ascii_slug(row.note)[:24]}"
                self.heartbeat(f"  [{i+1}/{len(matrix)}] {row.note}")
                _mark_event()  # task start counts as activity
                # Adjust exclude to be empty in normal path so legacy dir name matches
                adj = MatrixRow(
                    note=row.note, query=row.query, topk=row.topk, exclude=row.exclude,
                )
                fut = ex.submit(
                    _run_one_task, adj,
                    base_dir=task_out, engines=self.engines, config=self.config,
                    cookies_map=cookies_map, main_query=main_query,
                    fetcher_classes=self._fetcher_classes,
                )
                futures.append((fut, row))

            for fut, row in futures:
                try:
                    res = fut.result(timeout=self.config.task_timeout_s + 30)
                except concurrent.futures.TimeoutError:
                    res = TaskResult(
                        note=row.note, query=row.query, status=TaskStatus.TIMEOUT,
                    )
                except Exception as e:
                    res = TaskResult(
                        note=row.note, query=row.query, status=TaskStatus.FAILED,
                        error=f"{type(e).__name__}: {e}"[:200],
                    )
                results.append(res)
                _inc()
                self.heartbeat(f"  {TAG_OK if res.status == TaskStatus.SUCCESS else TAG_WARN} {row.note} ({res.status.value})")
                _mark_event()  # task completion counts as activity
                if time.time() - start > self.config.global_timeout_s:
                    self.heartbeat(f"{TAG_FIRE} global watchdog tripped, ending remaining tasks")
                    break

        stop_event.set()
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ascii_slug(s: str) -> str:
    """Filesystem-safe slug.

    previous regex stripped ALL non-ASCII, so any
    Chinese-heavy query (e.g. "黄金 走势") collapsed to "" and the
    output dir became "search__...". Now we keep CJK + Latin + digits
    and only strip Windows-reserved chars (backslash / * ? : " < > |)
    plus collapse whitespace.

    Modern filesystems (NTFS, APFS, ext4 with utf8) handle UTF-8
    filenames directly, so "黄金-走势" is a valid slug everywhere.
    """
    import re
    s = re.sub(r'[\\/:*?"<>|]+', '-', s)
    s = re.sub(r'\s+', '-', s.strip())
    return s[:40] or "search"


def _task_to_json(r: TaskResult) -> dict[str, Any]:
    """Serialize a :class:`TaskResult` for ``summary.json``.

    Includes the ``extra`` field (engine name, fallback_used, fallback_status,
    n_attempted) so downstream tooling can audit which engine actually
    served each task — crucial for diagnosing fallback chain behaviour
    without re-reading stderr logs.
    """
    out: dict[str, Any] = {
        "note": r.note,
        "query": r.query,
        "status": r.status.value,
        "url_count": r.url_count,
        "duration_seconds": round(r.duration_seconds, 2),
        "output_dir": str(r.output_dir) if r.output_dir else None,
        "error": r.error,
    }
    # extra carries per-task diagnostics (engine choice, fallback chain
    # behaviour, attempt count). Include it so the run can be audited.
    if r.extra:
        # Coerce Path objects to strings for JSON safety.
        out["extra"] = {
            k: (str(v) if hasattr(v, "__fspath__") else v)
            for k, v in r.extra.items()
        }
    return out


def _all_meta(aggregated: AggregatedResult) -> list[FetchResult]:
    return list(aggregated.all_meta)


__all__ = ["Orchestrator", "MatrixRow", "build_search_matrix_from_plan"]
