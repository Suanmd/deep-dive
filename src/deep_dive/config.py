"""Configuration loading.

All hardcoded paths / keys from the previous legacy ``deep-dive``
package have been removed. Everything is now configurable through:

1. **Environment variables** (``TAVILY_API_KEY``, ``TAVILY_API_KEY_BACKUP``,
   ``DEEP_DIVE_OUTPUT_DIR``, ``DEEP_DIVE_CONFIG``)
2. **YAML file** (``config/defaults.yaml`` in the repo, or
   ``~/.deep-dive/config.yaml`` in the user's home directory)
3. **CLI flags** (override YAML)
4. **Programmatic**: instantiate ``Config`` directly

Resolution order (highest priority first):
    CLI > environment > YAML file > built-in defaults
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .constants import (
    DEFAULT_GLOBAL_TIMEOUT_S,
    DEFAULT_HEARTBEAT_INTERVAL_S,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUTPUT_DIRNAME,
    DEFAULT_TASK_TIMEOUT_S,
    DEFAULTS_FILE_BASENAME,
    MIN_CHARS_DEFAULT,
    QUERY_CORE_ENTITY_MIN_HITRATE,
    QUERY_RELEVANCE_MIN_HITRATE,
)
from .constants import (
    __version__ as PKG_VERSION,
)

# ---------------------------------------------------------------------------
# Built-in defaults (lowest priority)
# ---------------------------------------------------------------------------

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "version": PKG_VERSION,
    "output_dir": DEFAULT_OUTPUT_DIRNAME,
    "depth": "normal",
    "lang": "auto",
    "freshness": "",
    "search_engine": "auto",
    "no_tavily": False,
    "max_workers": DEFAULT_MAX_WORKERS,
    "min_chars": MIN_CHARS_DEFAULT,
    "task_timeout_s": DEFAULT_TASK_TIMEOUT_S,
    "global_timeout_s": DEFAULT_GLOBAL_TIMEOUT_S,
    "heartbeat_interval_s": DEFAULT_HEARTBEAT_INTERVAL_S,
    "relevance": {
        "min_hitrate": QUERY_RELEVANCE_MIN_HITRATE,
        "core_entity_min_hitrate": QUERY_CORE_ENTITY_MIN_HITRATE,
    },
    "depth_config": {
        "quick": {"topk": 14, "max_queries": 4},
        "normal": {"topk": 18, "max_queries": 8},
        "full": {"topk": 22, "max_queries": 14},
    },
    "log_level": "INFO",
    "debug": False,
}


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Config:
    """Resolved, ready-to-use configuration for one run.

    Construct via :func:`load_config`. CLI flags should be applied *after*
    loading by setting attributes directly — Config is mutable by design
    so the CLI layer can override individual fields.
    """

    output_dir: Path = field(default_factory=lambda: Path(DEFAULT_OUTPUT_DIRNAME))
    depth: str = "normal"
    lang: str = "auto"
    freshness: str = ""
    search_engine: str = "auto"
    no_tavily: bool = False
    max_workers: int = DEFAULT_MAX_WORKERS
    min_chars: int = MIN_CHARS_DEFAULT  # URLs shorter than this are treated as low quality
    task_timeout_s: int = DEFAULT_TASK_TIMEOUT_S
    global_timeout_s: int = DEFAULT_GLOBAL_TIMEOUT_S
    heartbeat_interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S
    relevance_min_hitrate: float = QUERY_RELEVANCE_MIN_HITRATE
    core_entity_min_hitrate: float = QUERY_CORE_ENTITY_MIN_HITRATE
    depth_config: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "quick": {"topk": 14, "max_queries": 4},
            "normal": {"topk": 18, "max_queries": 8},
            "full": {"topk": 22, "max_queries": 14},
        }
    )
    log_level: str = "INFO"
    debug: bool = False

    # ------------------------------------------------------------------
    # Tavily API keys (multi-key)
    # ------------------------------------------------------------------
    # N-key support: pass ``tavily_keys=[k1, k2, k3]`` (or via YAML /
    # CLI). ``TavilyEngine`` will rotate across all configured keys,
    # trying each until one succeeds. ``tavily_api_key`` /
    # ``tavily_api_key_backup`` are kept for backwards compat and map
    # to ``tavily_keys=[primary, backup]`` if not overridden.
    #
    # Env-var fallback honoured by TavilyEngine itself:
    #     TAVILY_API_KEYS       comma-separated list (NEW, highest priority)
    #     TAVILY_API_KEY_BACKUP single backup key (legacy)
    #     TAVILY_API_KEY        single primary key (legacy)
    # (env vars are read by TavilyEngine in addition to these fields.)
    tavily_keys: list[str] = field(default_factory=list)
    tavily_api_key: str | None = None
    tavily_api_key_backup: str | None = None

    # ------------------------------------------------------------------
    # MMX invocations (multi-invocation)
    # ------------------------------------------------------------------
    # Each dict represents one (path, env, args) invocation profile:
    #     mmx_invocations:
    #       - name: personal
    #         path: null
    #         env: {MMX_API_KEY: personal-key}
    #         args: []
    #       - name: work
    #         path: null
    #         env: {MMX_API_KEY: work-key}
    #         args: [--profile=work]
    # Empty list → single default invocation using ``shutil.which("mmx")``.
    mmx_invocations: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------
    @property
    def cookie_file(self) -> Path | None:
        """Resolve the cookie file path.

        Order of precedence:
            1. ``DEEP_DIVE_COOKIE_FILE`` env var (absolute path)
            2. ``<output_dir>/../config/cookies.json`` (legacy layout)
            3. ``./config/cookies.json`` (next to cwd)
        Returns None if no candidate exists.
        """
        env = os.environ.get("DEEP_DIVE_COOKIE_FILE")
        if env:
            p = Path(env)
            return p if p.exists() else None
        # Legacy: output_dir sibling
        legacy = self.output_dir.parent / "config" / "cookies.json"
        if legacy.exists():
            return legacy
        cwd = Path.cwd() / "config" / "cookies.json"
        return cwd if cwd.exists() else None

    # ------------------------------------------------------------------
    # Depth helpers
    # ------------------------------------------------------------------
    def topk_for(self, depth: str | None = None) -> int:
        """Top-K value for the given (or current) depth.

        Falls back to the current depth's preset, then to ``"normal"``,
        then to a hard-coded sensible default (18).
        """
        depth = (depth or self.depth).lower()
        cfg = self.depth_config.get(depth) or self.depth_config.get("normal") or {"topk": 18}
        return int(cfg["topk"])

    def max_queries_for(self, depth: str | None = None) -> int:
        """Max number of matrix rows for the given (or current) depth.

        Falls back to the current depth's preset, then to ``"normal"``,
        then to a hard-coded sensible default (8).
        """
        depth = (depth or self.depth).lower()
        cfg = self.depth_config.get(depth) or self.depth_config.get("normal") or {"max_queries": 8}
        return int(cfg["max_queries"])

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Serialize this config to a dict (optionally redacting secrets).

        Args:
            redact_secrets: if True (default), replace ``tavily_api_key``,
                ``tavily_api_key_backup``, and ``tavily_keys`` with
                ``***REDACTED***`` / placeholder counts. Pass False to
                keep the real values (e.g. for local debugging).

        Returns:
            A JSON-serializable dict representation of this config.
        """
        d = {
            "version": PKG_VERSION,
            "output_dir": str(self.output_dir),
            "depth": self.depth,
            "lang": self.lang,
            "freshness": self.freshness,
            "search_engine": self.search_engine,
            "no_tavily": self.no_tavily,
            "max_workers": self.max_workers,
            "min_chars": self.min_chars,
            "task_timeout_s": self.task_timeout_s,
            "global_timeout_s": self.global_timeout_s,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "relevance_min_hitrate": self.relevance_min_hitrate,
            "core_entity_min_hitrate": self.core_entity_min_hitrate,
            "depth_config": self.depth_config,
            "log_level": self.log_level,
            "debug": self.debug,
            "tavily_keys": (
                ["***REDACTED***"] * len(self.tavily_keys)
                if (redact_secrets and self.tavily_keys)
                else list(self.tavily_keys)
            ),
            "tavily_api_key": "***REDACTED***"
            if (redact_secrets and self.tavily_api_key)
            else self.tavily_api_key,
            "tavily_api_key_backup": "***REDACTED***"
            if (redact_secrets and self.tavily_api_key_backup)
            else self.tavily_api_key_backup,
            "mmx_invocations": (
                "***REDACTED***" if (redact_secrets and self.mmx_invocations) else list(self.mmx_invocations)
            ),
        }
        return d


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _find_yaml_file() -> Path | None:
    """Locate the user YAML config, if any.

    Search order:
        1. ``DEEP_DIVE_CONFIG`` env var (absolute path)
        2. ``~/.deep-dive/config.yaml``
        3. ``<pkg_install_root>/config/defaults.yaml`` (package default)
    """
    env = os.environ.get("DEEP_DIVE_CONFIG")
    if env:
        p = Path(env)
        if p.exists():
            return p
    home = Path.home() / ".deep-dive" / "config.yaml"
    if home.exists():
        return home
    # Package-bundled default
    pkg_default = Path(__file__).resolve().parent.parent.parent / "config" / DEFAULTS_FILE_BASENAME
    if pkg_default.exists():
        return pkg_default
    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _apply_yaml(cfg: Config, data: dict[str, Any]) -> None:
    """Apply YAML keys onto a Config (best-effort, no exceptions)."""

    def _set(name: str, target_type: type) -> None:
        if name not in data:
            return
        with contextlib.suppress(TypeError, ValueError):
            setattr(cfg, name, target_type(data[name]))

    _set("depth", str)
    _set("lang", str)
    _set("freshness", str)
    _set("search_engine", str)
    _set("no_tavily", bool)
    _set("max_workers", int)
    _set("min_chars", int)
    _set("task_timeout_s", int)
    _set("global_timeout_s", int)
    _set("heartbeat_interval_s", int)
    _set("log_level", str)
    _set("debug", bool)
    if "output_dir" in data:
        cfg.output_dir = Path(str(data["output_dir"]))
    rel = data.get("relevance")
    if isinstance(rel, dict):
        if "min_hitrate" in rel:
            with contextlib.suppress(TypeError, ValueError):
                cfg.relevance_min_hitrate = float(rel["min_hitrate"])
        if "core_entity_min_hitrate" in rel:
            with contextlib.suppress(TypeError, ValueError):
                cfg.core_entity_min_hitrate = float(rel["core_entity_min_hitrate"])
    dc = data.get("depth_config")
    if isinstance(dc, dict):
        merged = dict(cfg.depth_config)
        for depth, vals in dc.items():
            if not isinstance(vals, dict):
                continue
            merged.setdefault(depth, {})
            for k, v in vals.items():
                with contextlib.suppress(TypeError, ValueError):
                    merged[depth][k] = int(v)
        cfg.depth_config = merged
    # multi-key support
    if isinstance(data.get("tavily_keys"), list):
        cfg.tavily_keys = [str(k) for k in data["tavily_keys"] if k]
    if isinstance(data.get("mmx_invocations"), list):
        cfg.mmx_invocations = [dict(inv) for inv in data["mmx_invocations"] if isinstance(inv, dict)]


def _apply_env(cfg: Config) -> None:
    """Pick up env-var overrides (highest priority after CLI).

    ``TAVILY_API_KEYS`` (comma-separated) is the canonical
    multi-key env var. Per-engine ``TavilyEngine._build_pool_from_args``
    also reads the legacy single-key env vars; we don't duplicate
    that logic here.
    """
    out = os.environ.get("DEEP_DIVE_OUTPUT_DIR")
    if out:
        cfg.output_dir = Path(out)
    if os.environ.get("DEEP_DIVE_DEBUG") == "1":
        cfg.debug = True
    cfg.tavily_api_key = os.environ.get("TAVILY_API_KEY") or None
    cfg.tavily_api_key_backup = os.environ.get("TAVILY_API_KEY_BACKUP") or None
    env_keys = os.environ.get("TAVILY_API_KEYS", "").strip()
    if env_keys:
        cfg.tavily_keys = [k.strip() for k in env_keys.split(",") if k.strip()]


def load_config(
    *,
    overrides: dict[str, Any] | None = None,
    config_file: Path | None = None,
) -> Config:
    """Resolve a :class:`Config` from defaults + YAML + env + overrides.

    Args:
        overrides: programmatic overrides applied last (e.g. from CLI args).
                   Keys mirror :class:`Config` attribute names. Nested dicts
                   are not supported — pass flat keys only.
        config_file: explicit YAML config path; if None, searches standard locations.

    Returns:
        A fully resolved Config instance.
    """
    cfg = Config()

    # 1. YAML
    yaml_path = config_file or _find_yaml_file()
    if yaml_path is not None:
        _apply_yaml(cfg, _load_yaml(yaml_path))

    # 2. Environment
    _apply_env(cfg)

    # 3. Overrides (highest priority)
    if overrides:
        for k, v in overrides.items():
            if not hasattr(cfg, k):
                continue
            if k == "output_dir" and v is not None:
                cfg.output_dir = Path(v)
            else:
                setattr(cfg, k, v)

    return cfg


__all__ = ["Config", "load_config"]
