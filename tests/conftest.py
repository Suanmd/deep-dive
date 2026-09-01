"""Shared pytest fixtures and configuration.

CRITICAL: this conftest inserts ``src/`` at position 0 of ``sys.path``
so the ``deep_dive`` package resolves to the source version
(``src/deep_dive/``) and NOT to any stray package at the project root.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup — make src/ importable without installing the package.
# Insert at index 0 so this version of `deep_dive` takes precedence over
# any unrelated package of the same name on sys.path.
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"

# Remove any pre-existing entry for the project root so its `deep_dive/`
# (if any) cannot shadow our src/deep_dive/ package.
sys.path = [p for p in sys.path if Path(p).resolve() != _ROOT.resolve()]
sys.path = [p for p in sys.path if Path(p).resolve() != _SRC.resolve()]

# Insert src first.
sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_deep_dive_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear all deep-dive-related env vars before each test.

    This makes config-loading tests deterministic regardless of the
    developer's shell environment.
    """
    env_vars = (
        "TAVILY_API_KEY",
        "TAVILY_API_KEY_BACKUP",
        "DEEP_DIVE_OUTPUT_DIR",
        "DEEP_DIVE_CONFIG",
        "DEEP_DIVE_COOKIE_FILE",
        "DEEP_DIVE_DEBUG",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
    )
    for v in env_vars:
        monkeypatch.delenv(v, raising=False)
    yield


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """A clean tmp directory for output. Returned as Path."""
    out = tmp_path / "deep-dive-out"
    out.mkdir()
    return out


@pytest.fixture
def tmp_raw_dir(tmp_path: Path) -> Path:
    """A ``raw/`` subdirectory under tmp_path."""
    raw = tmp_path / "raw"
    raw.mkdir()
    return raw


@pytest.fixture
def cookie_file(tmp_path: Path) -> Path:
    """Create a sample cookie file with two sites, return its path."""
    cookies = {
        "zhihu": {
            "domain": ".zhihu.com",
            "cookies": [
                {"name": "z_c0", "value": "abc123", "domain": ".zhihu.com", "path": "/"},
                {"name": "KLBRSID", "value": "x", "domain": ".zhihu.com", "path": "/"},
            ],
        },
        "baidu_wenku": {
            "domain": ".baidu.com",
            "cookies": [
                {"name": "BDUSS", "value": "fake-bduss", "domain": ".baidu.com", "path": "/"},
            ],
        },
    }
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def sample_metadata_json(tmp_path: Path) -> Path:
    """Create a sample ``metadata.json`` with mixed-status entries."""
    data = [
        {
            "url": "https://example.com/article-1",
            "title": "Example Article 1",
            "status": "success",
            "chars": 1500,
            "txt_file": "example_com_article_1.txt",
            "html_file": "example_com_article_1.html",
            "source_task": "中文原始",
            "query_index": 0,
        },
        {
            "url": "https://example.com/article-2",
            "title": "Example Article 2",
            "status": "success",
            "chars": 800,
            "txt_file": "example_com_article_2.txt",
            "html_file": "example_com_article_2.html",
            "source_task": "英文基础",
            "query_index": 1,
        },
        {
            "url": "https://example.com/blocked",
            "title": "",
            "status": "blocked",
            "chars": 0,
            "source_task": "中文细化",
            "query_index": 2,
        },
    ]
    p = tmp_path / "metadata.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


@pytest.fixture
def sample_report_md(tmp_path: Path) -> Path:
    """A minimal but parseable report.md for Capy summary tests."""
    body = """# Deep Dive Report: 长鑫存储

## 1. 任务执行情况
| # | 任务 | 状态 |
|---|------|------|
| 1 | 中文原始 | OK |

## 2. URL 来源汇总
1. 长鑫存储 DRAM 产能分析

## 3. 全文内容

### 1. 长鑫存储 DRAM 产能激增 30%
**URL**: https://example.com/article-1
**来源**: 中文原始

```
长鑫存储 2026 年 DRAM 产能预计激增 30%，
达到每月 20 万片晶圆。机构预测长鑫存储市场份额将提升至 8%。
```

---

### 2. 长鑫科技 美光诉讼进展
**URL**: https://example.com/article-2
**来源**: 英文基础

```
Long Xin DRAM production capacity expansion continues.
Analysts forecast Long Xin Technology will capture 8% market share by 2027.
```

---

### 3. 不相关：黄金价格分析
**URL**: https://example.com/article-3
**来源**: 中文细化

```
黄金价格 2026 年走势分析。机构预测金价将突破 3000 美元/盎司。
```

---

## 4. 元数据

```json
{"query": "长鑫存储"}
```
"""
    p = tmp_path / "report.md"
    p.write_text(body, encoding="utf-8")
    return p
