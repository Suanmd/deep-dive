"""Auto-rescue: build ``<topic>_raw_all.txt`` when dedup==0.

When the aggregator ends up with zero unique URLs (because every task
returned 0 URLs, or every URL was a duplicate of another), the report
would otherwise be empty. The auto-rescue scans the ``raw/`` directory
for any ``.txt`` files that the per-task pipelines actually wrote,
parses out paragraph-level SHA1 duplicates, and emits

    <topic_dir>/<topic_dir.name>_raw_all.txt

so the user always has a non-empty "fallback" document to read.

This is the auto-rescue behaviour, packaged as a reusable function.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Final

from deep_dive.constants import TAG_RESCUE
from deep_dive.logging_setup import safe_print
from deep_dive.types import AggregatedResult

_PARAGRAPH_MIN_LEN: Final[int] = 80
_SEPARATOR: Final[str] = "\n\n" + ("=" * 60) + "\n\n"


def _paragraph_hash(paragraph: str) -> str:
    """Stable SHA1 of a paragraph (whitespace-normalized).

    SHA1 is used here purely for content fingerprinting (deduplication
    of identical paragraphs across tasks), NOT for security. The
    ``usedforsecurity=False`` flag (Python 3.9+) silences Bandit's
    B324 weak-hash warning and makes intent explicit. SHA1 is
    acceptable for non-cryptographic hash use.
    """
    return hashlib.sha1(
        paragraph.strip().encode("utf-8", errors="ignore"),
        usedforsecurity=False,
    ).hexdigest()


def auto_rescue_raw(
    *,
    topic_dir: Path,
    raw_dir: Path,
    aggregated: AggregatedResult,
) -> tuple[int, int, str | None]:
    """Build ``<topic>_raw_all.txt`` if missing.

    Args:
        topic_dir: the topic directory (contains ``report.md``, ``summary.json``).
        raw_dir: the ``raw/`` directory inside ``topic_dir``.
        aggregated: aggregated results — used only to compute the
            default skip condition (when ``raw_all.txt`` already
            exists and is large enough).

    Returns:
        Tuple ``(n_files_rescued, total_unique_chars, output_path)``.
        ``output_path`` is ``None`` if nothing was written.

    Notes:
        * The old guard ``if aggregated.total_urls > 0: return``
          always allowed ``quota_exceeded`` tasks to
          contribute data. The new guard is "raw_all.txt already exists
          and is > 1 KB" — much simpler and matches user expectation.
    """
    out_path = topic_dir / f"{topic_dir.name}_raw_all.txt"
    if out_path.exists() and out_path.stat().st_size > 1000:
        safe_print(f"{TAG_RESCUE} skip: raw_all.txt already exists ({out_path.stat().st_size} chars)")
        return (0, 0, str(out_path))

    if not raw_dir.exists():
        safe_print(f"{TAG_RESCUE} no raw/ directory, skip")
        return (0, 0, None)

    txt_files: list[str] = []
    for root, _dirs, files in os.walk(str(raw_dir)):
        for fn in files:
            if fn.endswith(".txt") and not fn.endswith("_raw_all.txt"):
                txt_files.append(os.path.join(root, fn))

    if not txt_files:
        safe_print(f"{TAG_RESCUE} no .txt files in raw/, skip")
        return (0, 0, None)

    seen_hashes: set[str] = set()
    combined_chunks: list[str] = []
    total_chars = 0
    n_dedup_paragraphs = 0

    for tf in txt_files:
        try:
            with open(tf, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            continue
        paragraphs = re.split(r"\n\s*\n", text)
        file_unique: list[str] = []
        for para in paragraphs:
            norm = para.strip()
            if len(norm) < _PARAGRAPH_MIN_LEN:
                continue
            h = _paragraph_hash(norm)
            if h in seen_hashes:
                n_dedup_paragraphs += 1
                continue
            seen_hashes.add(h)
            file_unique.append(para)
        if file_unique:
            chunk = "\n\n".join(file_unique)
            combined_chunks.append(chunk)
            total_chars += len(chunk)

    if not combined_chunks:
        safe_print(f"{TAG_RESCUE} all paragraphs were duplicates, nothing to write")
        return (0, 0, None)

    out_path.write_text(_SEPARATOR.join(combined_chunks), encoding="utf-8-sig")
    safe_print(
        f"{TAG_RESCUE} rescued {len(txt_files)} files | "
        f"paragraphs deduped={n_dedup_paragraphs} | "
        f"unique chars={total_chars} -> {out_path.name}"
    )
    return (len(txt_files), total_chars, str(out_path))


__all__ = ["auto_rescue_raw"]
