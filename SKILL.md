---
name: deep-dive
description: "Deep research engine for agents. Multi-perspective parallel search (MMX + Tavily) + multi-language expansion + site targeting + full-text scraping (Playwright + cloudscraper fallback) + cookie injection + dedup aggregation + structured Markdown report output. CLI + Python API."
---

# deep-dive

> Deep research engine for agents. Multi-perspective parallel search + full-text
> scraping + cookie injection + dedup aggregation + structured Markdown report.

## What it does

Given a research query, `deep-dive` runs multiple parallel searches (different
angles, different languages, different sites), fetches the top results with a
real browser, deduplicates and relevance-filters them, and writes a four-section
Markdown report plus a Capy summary (theme clustering + key quotes +
bull/bear arguments).

## When to use

- You need a structured deep-research report on a topic (Chinese or English).
- You need to compare multiple angles of the same topic (technical, academic,
  critical, primary-source).
- You need site-specific deep dives (e.g., everything from arxiv.org about
  Transformer variants).
- You need login-walled content and have cookies configured.

## When NOT to use

- You need a single quick lookup. Use plain web search instead.
- You need real-time data. deep-dive's search engines have their own latency.
- You need exact-match quotes. Search engines may paraphrase; downstream
  scraping is best-effort.

## Usage

### CLI

```bash
deep-dive --query "<query-tech>" --depth normal
deep-dive --query "黄金 走势" --depth quick --no-capy
deep-dive --query "your topic" --depth full --output ./my-research
```

### Python module

```bash
python -m deep_dive --query "your topic" --depth normal
```

### Programmatic (LLM-driven mode)

The recommended path for agents: have the LLM produce a `ResearchPlan` JSON
describing variants, target sites, and English search terms; pass it via
`--plan` or programmatically:

```python
from deep_dive import Orchestrator, Config
from deep_dive.types import ResearchPlan

plan = ResearchPlan(
    query="<query-tech>",
    kind="tech",
    depth="normal",
    language_priority="balanced",
    english_search_terms=[
        "Attention Is All You Need Vaswani 2017 paper",
        "Transformer variants survey BERT GPT",
    ],
    variants={
        "refined": "transformer 架构 原理 自注意力",
        "critique": "transformer 局限 O(n²) 长序列",
        "academic": "transformer 综述 演进",
        "primary": "Attention Is All You Need 原文",
        "comparative": "transformer vs RNN vs CNN",
    },
    target_sites=["arxiv.org", "paperswithcode.com"],
    relevance_threshold=0.30,
    rationale="Five lines: original paper + critique + variants + code + comparison",
)
orch = Orchestrator(Config())
result = orch.run(query="<query-tech>", plan=plan)
```

## Configuration

User-tunable values live in **config files**, not in code:

| File | Purpose |
|------|---------|
| `config/defaults.yaml` | Engine, timeouts, depth caps, quality thresholds, low-quality domain list |
| `config/cookies.example.json` | Template; copy to `config/cookies.json` (gitignored) for login-walled sites |

Environment variables for API keys: `TAVILY_API_KEY`, `TAVILY_API_KEY_BACKUP`.

## Outputs

Each run creates `./tmp/deep-dive/<topic>__<run-id>/`:

- `report.md` — main report (4 sections + Capy summary)
- `summary.json` — task-level metadata
- `raw/` — per-task raw HTML/TXT and `metadata.json`
- `<topic>_raw_all.txt` — auto-rescue concatenated scrape (when dedup=0)
- `debug/` — per-step state (only with `--debug`)

## Module layout

```
src/deep_dive/
├── __init__.py        # Public API exports
├── cli.py             # argparse CLI entry
├── config.py          # Config dataclass + load_config()
├── constants.py       # Internal constants (TAG_* labels, etc.)
├── orchestrator.py    # Plan / matrix build + parallel dispatch
├── types.py           # All public dataclasses + enums
├── local_langs.py     # Local-language detection
├── query_classifier.py # Kind detection from query
├── query_variants.py  # Plan → variants
├── relevance.py       # Two-stage relevance check
├── aggregator.py      # Cross-task dedup
├── rescue.py          # auto_rescue_raw
├── logging_setup.py   # UTF-8 safe_print
├── crawler/
│   ├── cookies.py     # Cookie load + URL match
│   ├── encoding.py    # Response encoding detection
│   ├── extraction.py  # trafilatura wrapper
│   ├── pipeline.py    # Per-task fetch + extract + relevance
│   ├── blacklist.py   # Low-quality host patterns
│   ├── engines/       # MMXEngine, TavilyEngine, DuckDuckGoEngine
│   └── fetchers/      # PlaywrightFetcher, CloudScraperFetcher
├── filters/
│   ├── canonical.py   # URL canonicalisation
│   └── url_filter.py  # smart_filter_urls
└── reporting/
    ├── builder.py     # 4-section Markdown report
    └── capy_summary.py # Auto-generated Capy section
```

## Notes for OpenClaw users

- Place deep-dive at any path; OpenClaw auto-discovers `SKILL.md`.
- Runtime output goes to `<workspace>/tmp/deep-dive/` by default — adjust via
  `output_dir` in `config/defaults.yaml` or `--output` flag.
- For multi-language queries, set `--lang auto` (default) so local-language
  variants get added automatically.

## See also

- `docs/usage.md` — CLI cookbook
- `docs/architecture.md` — data flow diagram
- `docs/engines.md` — engine selection
- `docs/cookies.md` — cookie configuration
- `examples/` — runnable example scripts and plan JSONs
