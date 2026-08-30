"""Command-line interface for deep-dive.

All flags are listed in ``--help``. The CLI is intentionally minimal — heavy
logic lives in :mod:`deep_dive.orchestrator`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Config, load_config
from .logging_setup import safe_print
from .types import ResearchPlan

__all__ = ["main", "_build_parser"]

PKG_VERSION = "1.0.0"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deep-dive",
        description="Deep research engine: multi-perspective search + scraping + report",
    )
    p.add_argument("--query", "-q", required=True, help="Search topic")
    p.add_argument(
        "--depth",
        choices=["quick", "normal", "full"],
        default=None,
        help="Search depth preset (default: from config)",
    )
    p.add_argument(
        "--freshness",
        choices=["day", "week", "month", "year"],
        default=None,
        help="Time filter for search engines",
    )
    p.add_argument(
        "--lang",
        choices=["auto", "zh", "en"],
        default=None,
        help="Query language (default: auto-detect)",
    )
    p.add_argument(
        "--search-engine",
        choices=["auto", "mmx", "tavily"],
        default=None,
        help="Engine selection (default: auto = mmx + tavily fallback)",
    )
    p.add_argument(
        "--no-tavily",
        action="store_true",
        help="Force MMX-only (skip Tavily fallback)",
    )
    p.add_argument(
        "--tavily-key",
        action="append",
        default=None,
        metavar="KEY",
        help="Tavily API key (repeatable; env var TAVILY_API_KEY is preferred)",
    )
    p.add_argument(
        "--mmx-invocation",
        action="append",
        default=None,
        metavar="JSON",
        help="MMX invocation profile as JSON object (repeatable)",
    )
    p.add_argument("--output", "-o", default=None, help="Output root directory")
    p.add_argument("--run-id", default=None, help="Custom run id (ASCII slug enforced)")
    p.add_argument("--max-workers", type=int, default=None, help="Parallel task concurrency")
    p.add_argument(
        "--topk", type=int, default=None,
        help="Override per-task URL cap (default: from depth preset). "
             "Useful when search engines return fewer than the preset "
             "default — --topk=24 gives more room than normal's 18.",
    )
    p.add_argument("--min-chars", type=int, default=None, help="Low-quality page char threshold")
    p.add_argument("--no-report", action="store_true", help="Skip Markdown report generation")
    p.add_argument("--no-capy", action="store_true", help="Skip Capy summary section")
    p.add_argument("--debug", action="store_true", help="Persist per-step state to <topic_dir>/debug/")
    p.add_argument(
        "--plan",
        default=None,
        metavar="PATH",
        help=(
            "Path to ResearchPlan JSON (LLM-supplied research strategy). "
            "When supplied, deep-dive consumes plan.variants / "
            "plan.english_search_terms / plan.target_sites. "
            "See examples/plan_*.json for the schema. "
            "If omitted, deep-dive auto-generates a minimal plan from the query."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Load plan + compute search matrix but do NOT dispatch any tasks "
            "or make network calls. Prints plan summary, matrix rows, and any "
            "cap-truncated tasks, then exits 0. Use this to preview before a full run."
        ),
    )
    p.add_argument("--version", action="version", version=f"deep-dive {PKG_VERSION}")
    p.add_argument(
        "--print-config",
        action="store_true",
        help="Print resolved configuration as JSON and exit",
    )
    return p


def _apply_cli_overrides(cfg: Config, args: argparse.Namespace) -> bool:
    """Map argparse Namespace onto a Config instance.

    Returns ``True`` on success, ``False`` if any CLI flag had an
    invalid value (e.g. ``--mmx-invocation`` was not parseable JSON).
    The caller (``main()``) exits with code 2 on ``False`` so the
    user sees a non-zero exit and can correct the bad flag.
    """
    if args.depth is not None:
        cfg.depth = args.depth
    if args.freshness is not None:
        cfg.freshness = args.freshness
    if args.lang is not None:
        cfg.lang = args.lang
    if args.search_engine is not None:
        cfg.search_engine = args.search_engine
    if args.no_tavily:
        cfg.no_tavily = True
    if args.tavily_key:
        merged = list(cfg.tavily_keys) + [str(k) for k in args.tavily_key if k]
        cfg.tavily_keys = merged
    if args.mmx_invocation:
        parsed: list[dict] = []
        for raw in args.mmx_invocation:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                safe_print(f"[CLI] --mmx-invocation bad JSON: {e}")
                return False
            if not isinstance(obj, dict):
                safe_print("[CLI] --mmx-invocation must be a JSON object")
                return False
            parsed.append(obj)
        cfg.mmx_invocations = list(cfg.mmx_invocations) + parsed
    if args.output is not None:
        cfg.output_dir = Path(args.output)
    if args.max_workers is not None:
        cfg.max_workers = args.max_workers
    if args.topk is not None and args.topk > 0:
        # Overwrite topk for the current depth only. Other depths keep
        # their defaults from depth_config, so ``--depth=quick`` after
        # ``--topk=24`` still uses 14.
        d = cfg.depth_config.setdefault(cfg.depth, {})
        d["topk"] = args.topk
    if args.min_chars is not None:
        cfg.min_chars = args.min_chars
    if args.debug:
        cfg.debug = True
    return True


def _load_plan(path: str | None) -> ResearchPlan | None:
    """Load ResearchPlan from JSON file. Returns None when no path given."""
    if path is None:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        safe_print(f"[CLI] cannot read plan file {path!r}: {e}")
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        safe_print(f"[CLI] plan file {path!r} is not valid JSON: {e}")
        return None
    try:
        return ResearchPlan.from_dict(data)
    except (KeyError, TypeError, ValueError) as e:
        safe_print(f"[CLI] plan file {path!r} has invalid schema: {e}")
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Exit codes:
        0   normal completion (report + summary written, [DONE] printed)
        1   config load failure / orchestrator crash (traceback printed)
        130 user interrupted (SIGINT)

    ``safe_print(..., flush=True)`` and ``logging_setup._apply_encoding_fixes``
    take care of UTF-8 + line-buffered output. So even when stdout is
    piped (PowerShell, OpenClaw ``exec``), progress appears in real time.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except Exception as e:
        safe_print(f"[CONFIG] load failed: {e}")
        return 1

    if not _apply_cli_overrides(cfg, args):
        return 2  # user supplied an invalid CLI flag

    if args.print_config:
        safe_print(
            json.dumps(cfg.to_dict(redact_secrets=True), indent=2, ensure_ascii=False)
        )
        return 0

    plan = _load_plan(args.plan)
    if plan is None and args.plan is None:
        from .orchestrator import auto_plan
        plan = auto_plan(args.query)
        safe_print(
            "[INFO] No --plan supplied; auto-generated a minimal plan. "
            "Pass --plan for richer variants / target_sites (see examples/)."
        )
    elif plan is None:
        # --plan was given but failed to load
        return 1

    if args.dry_run:
        safe_print("")
        safe_print("=" * 70)
        safe_print(f"  DRY RUN | query='{args.query}' depth={cfg.depth}")
        safe_print("=" * 70)
        safe_print(f"[PLAN] kind={plan.kind} depth={plan.depth} priority={plan.language_priority}")
        safe_print(f"       variants={len(plan.variants)} sites={len(plan.target_sites)}")
        if plan.rationale:
            safe_print(f"       rationale: {plan.rationale}")
        safe_print("")
        safe_print("(network dispatch skipped — dry run)")
        return 0

    # Dispatch. Wrap the whole run in try/except so:
    #   - KeyboardInterrupt (Ctrl-C) → exit 130 (standard SIGINT code)
    #   - Any other crash → log traceback + force exit 1 via _os._exit
    #     (not ``return 1``) so the exit path is consistent with the
    #     success path and not subject to Playwright/asyncio GC
    #     finalizers that historically caused exit code 1 even on
    #     successful runs.
    import os as _os
    from .orchestrator import Orchestrator
    try:
        orch = Orchestrator(cfg)
        orch.run(query=args.query, plan=plan)
    except KeyboardInterrupt:
        safe_print("\n[ABORTED] user interrupted", file=sys.stderr, flush=True)
        _os._exit(130)
    except SystemExit:
        # honor explicit sys.exit() calls inside the orchestrator
        raise
    except Exception as e:
        safe_print(
            f"\n[FATAL] {type(e).__name__}: {e}",
            file=sys.stderr, flush=True,
        )
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        _os._exit(1)
    # Successful completion: bypass Python interpreter shutdown.
    # Playwright's async browser + per-fetch asyncio loops can leave
    # dangling thread state that surfaces as exit code 1 at GC time
    # even though the actual run completed cleanly (report.md +
    # summary.json are already on disk by this point). ``os._exit``
    # skips atexit handlers + GC finalizers, which is fine here — the
    # orchestrator has already done its own cleanup and written all
    # outputs.
    _os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
