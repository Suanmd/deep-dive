"""Core dataclasses used across the package.

All public domain types are defined here so the rest of the package can use
them without circular imports. Types are intentionally lightweight
(``@dataclass(frozen=True, slots=True)``) so they can be created cheaply in
the per-task hot path.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, enum.Enum):
    """Outcome of a single matrix task (one query → one set of URLs)."""

    SUCCESS = "success"
    QUOTA_EXCEEDED = "quota_exceeded"
    NO_RESULTS = "no_results"
    FAILED = "failed"
    TIMEOUT = "timeout"


class FetchStatus(str, enum.Enum):
    """Outcome of a single URL fetch."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    IRRELEVANT = "irrelevant"


class EngineKind(str, enum.Enum):
    """Which search engine to use for a given task."""

    MMX = "mmx"
    TAVILY = "tavily"
    AUTO = "auto"


class RelevanceVerdict(str, enum.Enum):
    """Three-stage query-relevance verdict.

    Stage 1: keyword density (single-char hit rate)
    Stage 2: core-entity coverage
    Stage 3: primary entity in lead (topic-drift guard — rejects articles
             that mention the topic but aren't *about* it).
    """

    RELEVANT = "relevant"
    IRRELEVANT_DENSITY = "irrelevant_density"
    IRRELEVANT_ENTITY = "irrelevant_entity"
    IRRELEVANT_LEAD = "irrelevant_lead"


class QueryKind(str, enum.Enum):
    """The built-in query templates + general fallback."""

    HUMANITIES = "humanities"
    TECH = "tech"
    ACADEMIC = "academic"
    NEWS = "news"
    BUSINESS = "business"
    MOVIES = "movies"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Search results (engine output)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single URL returned by a search engine."""

    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""           # "mmx" / "tavily"
    score: float | None = None


# ---------------------------------------------------------------------------
# Per-task and per-URL outcomes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FetchResult:
    """The result of trying to fetch + extract text from one URL."""

    url: str
    status: FetchStatus
    title: str = ""
    chars: int = 0
    html_path: Path | None = None
    txt_path: Path | None = None
    error: str | None = None
    source_task: str = ""
    query_index: int = -1
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """One row of the search matrix.

    A row represents a single parallel task in the orchestrator — a
    (query, topk, exclude, engine) tuple that the dispatcher will
    execute in its own thread.
    """

    note: str                       # human-readable label, e.g. "中文原始"
    query: str                      # the actual query string to send to the engine
    topk: int                       # desired number of URLs from the engine
    exclude: tuple[str, ...]        # blacklist domains (-site: ... appended to query)
    engine: str = "auto"            # engine hint: "auto" | "mmx" | "tavily"


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Aggregate result for one matrix task (one query → many URLs)."""

    note: str                              # human-readable label, e.g. "中文原始"
    query: str                             # the actual query that was searched
    status: TaskStatus
    output_dir: Path | None = None
    url_count: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CrawlResult:
    """Convenience container for a pipeline run.

    Holds the task results together with the aggregated cross-task summary.
    Mirrors what gets persisted into ``summary.json``.
    """

    topic: str
    run_id: str
    task_results: tuple[TaskResult, ...]
    aggregated: "AggregatedResult"
    report_path: Path | None = None
    global_status: str = "success"     # success / quota_exceeded / no_results / mixed


@dataclass(frozen=True, slots=True)
class AggregatedResult:
    """The merged, de-duplicated output across all tasks of one run."""

    total_urls: int
    unique_urls: tuple[str, ...]
    url_meta: dict[str, FetchResult]              # canonical_url → metadata
    all_meta: tuple[FetchResult, ...]             # all (incl. duplicates) for debug
    url_sources: dict[str, tuple[str, ...]]       # canonical_url → tuple of task notes
    url_query_indices: dict[str, tuple[int, ...]]
    global_status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict (Path → str, tuples → lists)."""
        return {
            "total_urls": self.total_urls,
            "unique_urls": list(self.unique_urls),
            "url_meta": {
                u: {
                    "url": m.url,
                    "status": m.status.value,
                    "title": m.title,
                    "chars": m.chars,
                    "html_file": str(m.html_path) if m.html_path else None,
                    "txt_file": str(m.txt_path) if m.txt_path else None,
                    "source_task": m.source_task,
                    "query_index": m.query_index,
                    "error": m.error,
                    **m.extra,
                }
                for u, m in self.url_meta.items()
            },
            "url_sources": {u: list(v) for u, v in self.url_sources.items()},
            "url_query_indices": {u: list(v) for u, v in self.url_query_indices.items()},
            "global_status": self.global_status,
        }


# ---------------------------------------------------------------------------
# ResearchPlan (LLM-supplied research strategy)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ResearchPlan:
    """LLM-supplied research plan.

    The orchestrator consumes ``variants`` / ``english_search_terms`` /
    ``target_sites`` directly. Without a plan, ``Orchestrator.run()``
    raises ``ValueError`` — callers (CLI or programmatic) must supply one.

    Fields:
        query: original user query (echoed for audit / debugging).
        kind: one of the QueryKind values — ``"humanities"``,
            ``"tech"``, ``"academic"``, ``"news"``, ``"business"``,
            or ``"general"``. Decided by the LLM based on intent.
        depth: ``"quick"`` / ``"normal"`` / ``"full"``.
        language_priority: ``"zh-only"`` / ``"en-only"`` /
            ``"zh-primary"`` / ``"balanced"``. Controls how many
            English tasks are emitted.
        english_search_terms: cross-language search queries the LLM
            chose. Replaces ``_chinese_to_english()``. The first entry
            is the English baseline; later entries become ``en_variant``
            / ``en_academic`` if present.
        variants: dict of variant_key → refined query. Recognised keys:
            ``"refined"``, ``"critique"``, ``"academic"``, ``"primary"``,
            ``"comparative"``. Replaces ``generate_variants()`` suffixes.
        target_sites: ordered list of ``site:`` directives. Most relevant
            first. Replaces ``site_targets`` + ``dynamic_site_targets``.
        relevance_threshold: 0.1-0.5 (overrides ``QUERY_RELEVANCE_MIN_HITRATE``).
        rationale: one-line human-readable justification (debug aid).
    """

    query: str
    kind: str = "general"
    depth: str = "normal"
    language_priority: str = "balanced"
    english_search_terms: tuple[str, ...] = ()
    variants: dict[str, str] = field(default_factory=dict)
    target_sites: tuple[str, ...] = ()
    relevance_threshold: float = 0.30
    rationale: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResearchPlan":
        """Build from a dict (e.g. parsed JSON).

        Tolerates extra keys (forward-compat with future plan fields).
        Validates that ``query`` is a non-empty string.
        """
        if "query" not in d or not isinstance(d["query"], str) or not d["query"].strip():
            raise ValueError("ResearchPlan.from_dict: missing or empty 'query' string")
        return cls(
            query=d["query"].strip(),
            kind=str(d.get("kind", "general")),
            depth=str(d.get("depth", "normal")),
            language_priority=str(d.get("language_priority", "balanced")),
            english_search_terms=tuple(str(t) for t in d.get("english_search_terms", ())),
            variants={str(k): str(v) for k, v in d.get("variants", {}).items()},
            target_sites=tuple(str(s) for s in d.get("target_sites", ())),
            relevance_threshold=float(d.get("relevance_threshold", 0.30)),
            rationale=str(d.get("rationale", "")),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "ResearchPlan":
        """Load from a JSON file."""
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            d = _json.load(f)
        if not isinstance(d, dict):
            raise ValueError(
                f"ResearchPlan JSON must be an object, got {type(d).__name__}"
            )
        return cls.from_dict(d)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "query": self.query,
            "kind": self.kind,
            "depth": self.depth,
            "language_priority": self.language_priority,
            "english_search_terms": list(self.english_search_terms),
            "variants": dict(self.variants),
            "target_sites": list(self.target_sites),
            "relevance_threshold": self.relevance_threshold,
            "rationale": self.rationale,
        }

    def to_json_file(self, path: Path) -> None:
        """Write to a JSON file (UTF-8, indented, ``ensure_ascii=False``)."""
        import json as _json
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


# Tech acronyms that unambiguously mark a query as AI/ML regardless of
# surrounding context. Curated rather than regex-derived because the
# set is small and we want to avoid false positives from common English
# words ("go", "do", "agent", "grok", "claude", "gemini", "llama" all
# have non-tech meanings and are intentionally excluded). Match is
# case-insensitive on the compact query (whitespace + dashes stripped)
# so "mHC", "MHC", and "mhc" all trigger.
_TECH_ACRONYMS: frozenset[str] = frozenset({
    # AI model architectures (no false positive risk)
    "mhc", "moe", "mha", "ssm", "mamba", "transformer", "vit",
    # LLM / multimodal model families (acronyms only — full names like
    # "claude" / "gemini" / "llama" have non-tech meanings and are
    # intentionally NOT here)
    "llm", "gpt", "bert", "t5", "vlm", "mlm",
    # Training paradigms
    "rlhf", "dpo", "ppo", "sft", "lora", "qlora",
    # Inference / serving patterns
    "rag", "mcp", "kvcache", "vllm", "gguf",
    # Compound brand names (specific enough to be unambiguous)
    "chatgpt", "deepseek", "qwen", "huggingface", "langchain",
})

# Major AI lab company names whose presence is an unambiguous tech
# signal in contemporary discourse. Matched **lowercased** so
# ``Anthropic`` / ``anthropic`` both hit. Excludes names with strong
# non-tech meanings (claude/gemini/llama are also personal names /
# zodiac / animal, mistral is also a Mediterranean wind).
_AI_LAB_BRANDS: frozenset[str] = frozenset({
    "anthropic",   # Claude
    "openai",      # ChatGPT, GPT
    "xai",         # Grok
    "mistral",     # Mistral / Mixtral
    "cohere",      # Command
    "perplexity",  # Perplexity AI
})

# Keyword sets for kind auto-detection from query. Kept in the types
# module (not config) because they are part of the SKILL contract, not
# user-tunable.
KIND_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    "tech": {
        # Chinese tech signals. "架构"/"论文"/"benchmark" added so that
        # queries like "deepseek mHC 架构" or "Llama 论文" classify as
        # tech even when no English keyword matches.
        "zh": (
            "技术", "编程", "代码", "算法", "深度学习", "机器学习",
            "大模型", "神经网络", "transformer", "gpt", "llm",
            "pytorch", "tensorflow", "github", "api", "sql",
            "kubernetes", "docker", "python", "java", "rust",
            "javascript", "go语言",
            "架构", "论文", "预印本", "微调", "对齐", "蒸馏",
            "量化", "剪枝", "评测", "基准", "实现", "推理",
            "智能体",
        ),
        # English tech signals. "architecture"/"paper"/"benchmark"/"arxiv"
        # added for the same reason as above.
        #
        # Intentionally NOT included: "agent" / "agents". In English these
        # are too ambiguous (real estate agent, secret agent, sales agent)
        # and trigger false-positive tech classification. Chinese "智能体"
        # above is kept because Chinese usage is much more specific to
        # AI/LLM agents.
        "en": (
            "tech", "code", "coding", "algorithm", "machine learning",
            "deep learning", "neural", "transformer", "pytorch",
            "tensorflow", "github", "api", "programming", "kubernetes",
            "docker", "python", "java", "rust", "javascript",
            "asyncio", "linux", "quantum", "robotics",
            "architecture", "paper", "arxiv", "preprint", "fine-tune",
            "finetune", "alignment", "quantization", "distillation",
            "benchmark", "evaluation", "inference",
        ),
    },
    "business": {
        "zh": ("公司", "投资", "股价", "估值", "市场", "财报", "ipo", "股票", "基金", "营收", "市值", "融资", "创业", "vc", "pe", "a股", "港股"),
        "en": ("company", "stock", "market", "investment", "ipo", "valuation", "revenue", "fund", "vc", "startup", "equity", "earnings", "finance", "investor"),
    },
    "news": {
        "zh": ("新闻", "今日", "最新", "突发", "今天", "昨日", "本周", "本月", "事件", "发生", "传言", "爆料", "回应"),
        "en": ("news", "today", "yesterday", "breaking", "latest", "this week", "this month", "happened", "occurred", "scandal", "rumor", "responds"),
    },
    "movies": {
        # Pop-culture / movies / TV / anime — the angle that wants
        # review/interpretation, themes/metaphor, box-office reception,
        # series/sequel framing — NOT academic survey/critique.
        "zh": ("电影", "动画片", "番剧", "动漫", "电视剧", "综艺", "剧场版", "ova", "动画", "剧集", "续集", "系列电影", "影评", "票房"),
        "en": ("movie", "movies", "film", "films", "anime", "tv series", "tv show", "series", "episode", "season", "sequel", "prequel", "cinema", "box office", "review", "soundtrack", "director", "screenplay"),
    },
    "academic": {
        "zh": ("论文", "学术", "研究", "期刊", "学报", "会议", "引用", "综述", "方法论", "实验", "数据集", "基线", "预印本"),
        "en": ("paper", "research", "study", "journal", "conference", "citation", "review", "methodology", "arxiv", "baseline", "dataset", "experiment", "preprint"),
    },
    "humanities": {
        "zh": ("历史", "文化", "哲学", "艺术", "书评", "小说", "文学", "心理", "精神", "访谈", "社会学", "人类学", "诗", "电影", "音乐", "思想", "传记", "回忆录", "天才", "疯子", "天才在左", "疯子在右", "抑郁症", "心理访谈", "人生", "灵魂", "真实"),
        "en": ("history", "culture", "philosophy", "art", "literature", "psychology", "psychiatry", "interview", "sociology", "anthropology", "poetry", "film", "music", "thought", "biography", "memoir", "genius", "madness", "lunatic", "depression", "soul", "reality", "life", "autobiography"),
    },
}


def detect_kind(query: str) -> str:
    """Keyword-based kind detection. Returns one of QueryKind values.

    Two-stage classification:

      1. **Acronym override** — if the query contains any well-known
         AI/ML acronym (see :data:`_TECH_ACRONYMS`), short-circuit to
         ``"tech"``. This catches queries like ``"mHC 是什么"`` or
         ``"Llama 3 论文"`` where the keyword scan finds nothing but
         the acronym is unambiguous.
      2. **Keyword scoring** — count substring hits per kind (Chinese
         keywords case-sensitive, English keywords lowercased). Tie
         → first kind in declaration order (tech > business > news >
         movies > academic > humanities).

    Falls back to ``"general"`` when no category scores above 0. The
    function is intentionally simple — the LLM is expected to override
    via ``--plan`` when it disagrees.
    """
    # Stage 1: acronym override (case-insensitive, whitespace-tolerant).
    q_compact = query.lower().replace(" ", "").replace("-", "").replace("_", "")
    if any(ac in q_compact for ac in _TECH_ACRONYMS):
        return "tech"

    # Stage 1b: AI-lab brand override. Distinct from acronyms (those
    # are product / architecture names; brands are company names).
    # Helps queries like ``Anthropic公司`` route to tech instead of
    # getting outvoted by the ``公司`` business keyword.
    if any(brand in q_compact for brand in _AI_LAB_BRANDS):
        return "tech"

    # Stage 2: keyword scoring.
    q_lower = query.lower()
    scores: dict[str, int] = {}
    for kind, langs in KIND_KEYWORDS.items():
        count = 0
        for kw in langs.get("zh", ()):
            if kw in query:  # case-sensitive for CJK
                count += 1
        for kw in langs.get("en", ()):
            if kw in q_lower:
                count += 1
        scores[kind] = count
    best = max(scores.values()) if scores else 0
    if best == 0:
        return "general"
    for kind, score in scores.items():
        if score == best:
            return kind
    return "general"
