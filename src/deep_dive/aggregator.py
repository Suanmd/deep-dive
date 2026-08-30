"""Cross-task URL aggregator.

Responsibilities
----------------

1. Walk all task output dirs, read ``metadata.json`` from each.
2. Build a de-duplicated URL → metadata map (canonical URLs).
3. Track per-URL "source tasks" and "query indices" for traceability.
4. Compute a global status (``success`` / ``quota_exceeded`` /
   ``no_results`` / ``mixed``).

This is the dedup logic with the following invariants:

* Don't drop ``quota_exceeded`` tasks — they may still contain usable
  raw data. Only ``failed`` tasks are excluded.
* When the same URL is fetched by multiple tasks, prefer the metadata
  with a non-empty title.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deep_dive.constants import TAG_WARN
from deep_dive.logging_setup import safe_print
from deep_dive.types import (
    AggregatedResult,
    FetchResult,
    FetchStatus,
    TaskResult,
    TaskStatus,
)


@dataclass(slots=True)
class Aggregator:
    """Stateless aggregator — instances are cheap to create."""

    def aggregate(
        self,
        task_results: Iterable[TaskResult],
        raw_dir: Path,
    ) -> AggregatedResult:
        """Aggregate per-task results into a single :class:`AggregatedResult`.

        Args:
            task_results: results from each matrix row.
            raw_dir: the ``raw/`` directory (used to find per-task
                ``metadata.json`` files).

        Returns:
            Aggregated results.
        """
        url_to_meta: dict[str, FetchResult] = {}
        all_meta: list[FetchResult] = []
        url_sources: dict[str, list[str]] = defaultdict(list)
        url_query_indices: dict[str, list[int]] = defaultdict(list)

        for idx, tr in enumerate(task_results):
            if tr.status == TaskStatus.FAILED or tr.output_dir is None:
                continue
            meta_path = tr.output_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                entries = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                safe_print(f"{TAG_WARN} metadata parse failed for {meta_path}: {e}")
                continue
            if not isinstance(entries, list):
                continue

            seen_in_task: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                fr = _entry_to_fetch_result(entry, source_task=tr.note, query_index=idx)
                all_meta.append(fr)
                if fr.status != FetchStatus.SUCCESS:
                    continue
                url = fr.url
                if url not in seen_in_task:
                    seen_in_task.add(url)
                    url_query_indices[url].append(idx)
                    if url not in url_to_meta:
                        url_to_meta[url] = fr
                    else:
                        # Prefer metadata with a non-empty title
                        if fr.title and not url_to_meta[url].title:
                            url_to_meta[url] = fr
                url_sources[url].append(tr.note)

        # Content-fingerprint dedup pass.
        # URL-level dedup (above) misses cases where the same article is
        # indexed under different URLs (HuggingFace dataset mirrors,
        # CDN variants, archive.org vs current URL, etc.). We compute a
        # sha256 fingerprint from (title + first 2 KB of normalized
        # text) and dedup by fingerprint. The first-seen wins; ties go
        # to the entry with a non-empty title, then to the longer chars.
        #
        # Cost: O(n) file reads of at most 2 KB each. With 60-80 URLs
        # typical, this is ~100-200 ms total. Acceptable for the
        # report-generation step (not in the search hot path).
        content_dedup_removed = _content_fingerprint_dedup(
            url_to_meta, url_sources, url_query_indices,
        )
        if content_dedup_removed:
            safe_print(
                f"[AGG] content-fingerprint dedup: removed "
                f"{content_dedup_removed} duplicate URL(s) "
                f"(same article indexed under multiple URLs)"
            )

        # Paraphrase dedup (5-gram Jaccard) — catches aggregator spam
        # that rewrites the same press release / product description.
        # Sits AFTER fingerprint dedup so the expensive pairwise scan
        # only sees genuinely distinct URLs.
        paraphrase_removed = _paraphrase_dedup(
            url_to_meta, url_sources, url_query_indices,
        )
        if paraphrase_removed:
            safe_print(
                f"[AGG] paraphrase dedup: removed {paraphrase_removed} "
                f"near-duplicate URL(s) (5-gram Jaccard > "
                f"{_PARAPHRASE_JACCARD_THRESHOLD})"
            )

        unique_urls = tuple(url_to_meta.keys())

        # Resolve n_total (the aggregator doesn't mutate the input)
        try:
            n_total = len(task_results)
        except TypeError:
            n_total = 0

        n_quota = sum(1 for r in task_results if r.status == TaskStatus.QUOTA_EXCEEDED)
        n_empty = sum(1 for r in task_results if r.status == TaskStatus.NO_RESULTS)
        n_success_tasks = sum(1 for r in task_results if r.status == TaskStatus.SUCCESS)

        if n_total == 0:
            global_status = "success"
        elif n_quota > n_total / 2:
            global_status = "quota_exceeded"
        elif n_empty == n_total:
            global_status = "no_results"
        elif n_success_tasks != n_total:
            global_status = "mixed"
        else:
            global_status = "success"

        return AggregatedResult(
            total_urls=len(unique_urls),
            unique_urls=unique_urls,
            url_meta=url_to_meta,
            all_meta=tuple(all_meta),
            url_sources={u: tuple(v) for u, v in url_sources.items()},
            url_query_indices={u: tuple(v) for u, v in url_query_indices.items()},
            global_status=global_status,
        )


def _entry_to_fetch_result(
    entry: dict[str, Any],
    *,
    source_task: str,
    query_index: int,
) -> FetchResult:
    """Translate a raw ``metadata.json`` entry to :class:`FetchResult`."""
    url = entry.get("url") or ""
    status_raw = entry.get("status") or "pending"
    try:
        status = FetchStatus(status_raw)
    except ValueError:
        status = FetchStatus.FAILED

    html_path = entry.get("html_file") or entry.get("html_path")
    txt_path = entry.get("txt_file") or entry.get("txt_path")

    return FetchResult(
        url=url,
        status=status,
        title=entry.get("title", "") or "",
        chars=int(entry.get("chars", 0) or 0),
        html_path=Path(html_path) if html_path else None,
        txt_path=Path(txt_path) if txt_path else None,
        error=entry.get("error"),
        source_task=source_task or entry.get("source_task", ""),
        query_index=query_index if query_index >= 0 else int(entry.get("query_index", -1)),
        extra={
            k: v
            for k, v in entry.items()
            if k not in {"url", "status", "title", "chars", "html_file", "html_path",
                         "txt_file", "txt_path", "error", "source_task", "query_index"}
        },
    )


__all__ = ["Aggregator"]


# ---------------------------------------------------------------------------
# Content-fingerprint dedup helpers
# ---------------------------------------------------------------------------

# Read this many bytes from each .txt file when computing fingerprints.
# 2 KB is enough to capture title + lede (most articles fingerprint
# within the first paragraph); small enough that 80 URLs ≈ 160 KB total
# read, completing in <100 ms even on slow disks.
_FP_TEXT_BYTES = 2048


def _content_fingerprint(
    fr: FetchResult,
) -> str | None:
    """Compute a content fingerprint for a fetched URL.

    Design choice (second iteration after test failure):
        fingerprint is based on **body content only**, not title. Reason:
        mirrors of the same article often have different titles (e.g.
        HuggingFace dataset card may say "AI Model Leaderboards — Live
        Rankings" on the dataset page but "lmarena-ai/leaderboard-dataset"
        on a code-search mirror). Body content is the stable signal.

    Returns:
        16-char SHA-256 prefix of the first 1 KB of normalised body
        text. Falls back to title-only when body is unavailable.
        Returns ``None`` if both are empty (URL is treated as unique).
    """
    body_head = ""
    txt_path = fr.txt_path
    if txt_path is not None and txt_path.exists():
        try:
            with txt_path.open("rb") as f:
                raw = f.read(_FP_TEXT_BYTES)
            text = raw.decode("utf-8", errors="replace")
            body_head = "".join(text.split())[:1024]
        except OSError:
            pass
    if body_head:
        return hashlib.sha256(body_head.encode("utf-8")).hexdigest()[:16]
    # No body available — fall back to title-only fingerprint.
    title = (fr.title or "").strip()
    if title:
        return hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    return None


def _is_better_candidate(new: FetchResult, existing: FetchResult) -> bool:
    """Return True if ``new`` should replace ``existing`` as the canonical
    entry for a duplicated content fingerprint.

    Preference order:
        1. Has a non-empty title (vs empty).
        2. Tie-break on ``chars`` (longer body = more authoritative).
    """
    new_has_title = bool(new.title and new.title.strip())
    existing_has_title = bool(existing.title and existing.title.strip())
    if new_has_title != existing_has_title:
        return new_has_title
    return (new.chars or 0) > (existing.chars or 0)


def _content_fingerprint_dedup(
    url_to_meta: dict[str, FetchResult],
    url_sources: dict[str, list[str]],
    url_query_indices: dict[str, list[int]],
) -> int:
    """Run content-fingerprint dedup on ``url_to_meta``.

    For each URL with a successful fetch, compute a fingerprint. URLs
    sharing the same fingerprint are considered duplicates of the same
    article; we keep the best one (see :func:`_is_better_candidate`)
    and remove the rest.

    The dedup is **idempotent** under URL-stable dedup (canonical URLs
    are already deduplicated separately, so within a single run, a
    fingerprint only collides between *different* URLs — i.e. genuinely
    mirrored content).

    Returns:
        Number of URLs removed.
    """
    fp_to_url: dict[str, str] = {}
    removed = 0
    for url, fr in list(url_to_meta.items()):
        fp = _content_fingerprint(fr)
        if fp is None:
            continue
        if fp in fp_to_url:
            existing_url = fp_to_url[fp]
            existing_fr = url_to_meta[existing_url]
            if _is_better_candidate(fr, existing_fr):
                # New wins — replace existing with new.
                url_to_meta[url] = fr
                del url_to_meta[existing_url]
                url_sources[url] = url_sources.pop(existing_url, [])
                url_query_indices[url] = url_query_indices.pop(existing_url, [])
                fp_to_url[fp] = url
                removed += 1
            else:
                # Existing wins — drop new.
                del url_to_meta[url]
                url_sources.pop(url, None)
                url_query_indices.pop(url, None)
                removed += 1
        else:
            fp_to_url[fp] = url
    return removed


# ---------------------------------------------------------------------------
# Paraphrase dedup (5-gram Jaccard)
# ---------------------------------------------------------------------------
#
# Distinct URLs can publish substantially the same article: AI tool
# aggregators rewriting the same press release, news wires reposting,
# CMS mirrors, etc. SHA fingerprint dedup above misses these because
# any non-trivial paraphrase changes the byte stream. We catch them
# with a 5-gram Jaccard similarity scan over the first
# ``_PARAPHRASE_TEXT_CHARS`` bytes of each URL's extracted body.
#
# Threshold calibration (from ``verify_paraphrase_dedup.py``,
# realistic-length ~350-char Chinese articles on the same topic):
#
#     paraphrase vs paraphrase  -> J ≈ 0.40  (significant overlap of
#                                              shared 5-grams like
#                                              product names, model
#                                              lists, dates)
#     paraphrase vs distinct    -> J ≈ 0.00  (no shared vocabulary
#                                              at all)
#
# Initial guess of 0.75 was wrong — 5-grams are position-bound so any
# word-order change in a paraphrase destroys many 5-grams. We use
# 0.30 which clears the paraphrase test by ~0.10 margin while
# keeping the distinct case at 0.00. Trade-off: two genuine
# reviews on the same topic (e.g. two WorkBuddy reviews sharing
# only product names + key features) could falsely merge here.
# For tighter precision, swap in semantic embeddings
# (sentence-transformers) — left as a TODO because the dependency
# cost (~400 MB model) is significant.

_PARAPHRASE_JACCARD_THRESHOLD = 0.30
# Stricter threshold when both URLs share the same host. Site-wide
# chrome (nav, footer, branding) inflates cross-page 5-gram overlap
# without implying semantic duplication. 0.85 = effectively same page.
_PARAPHRASE_SAME_DOMAIN_THRESHOLD = 0.85
_PARAPHRASE_TEXT_CHARS = 5000
_PARAPHRASE_NGRAM = 5


def _ngram_set(text: str, n: int = _PARAPHRASE_NGRAM) -> frozenset[str]:
    """Return the set of length-``n`` character n-grams over ``text``.

    Whitespace is preserved as-is by the caller (see
    :func:`_paraphrase_dedup` which pre-normalises by collapsing all
    whitespace to empty string before passing in). Returns an empty
    frozenset when the input is too short to contain any n-gram.
    """
    if not text or len(text) < n:
        return frozenset()
    return frozenset(text[i:i + n] for i in range(len(text) - n + 1))


def _paraphrase_dedup(
    url_to_meta: dict[str, FetchResult],
    url_sources: dict[str, list[str]],
    url_query_indices: dict[str, list[int]],
) -> int:
    """Drop URLs whose body text is a near-paraphrase of a longer one.

    Pairs are evaluated greedily by ``chars`` (descending). For each
    candidate URL, every shorter URL whose 5-gram Jaccard similarity
    exceeds :data:`_PARAPHRASE_JACCARD_THRESHOLD` is removed; the
    candidate (longer body, presumed more authoritative) is kept.

    Cost: O(n²) pairwise comparison over ``len(url_to_meta)`` URLs,
    each pair doing two set ops on up to ~5K n-grams. For n=80 this
    is ~16M ops ≈ 1-3 s in pure Python — acceptable because dedup
    runs once per run during report generation (outside the search
    hot path). For n>200, swap to minhash + LSH.

    Returns:
        Number of URLs removed.
    """
    if not url_to_meta:
        return 0

    # 1. Build n-gram sets.
    ngrams: dict[str, frozenset[str]] = {}
    for url, fr in url_to_meta.items():
        text = ""
        if fr.txt_path is not None and fr.txt_path.exists():
            try:
                with fr.txt_path.open("rb") as f:
                    raw = f.read(_PARAPHRASE_TEXT_CHARS)
                # Collapse all whitespace (incl. CJK full-width) so
                # n-grams cross word boundaries consistently. This is
                # the only normalisation we need for Jaccard to work
                # on Chinese + English mixed text.
                text = "".join(raw.decode("utf-8", errors="replace").split())
            except OSError:
                pass
        if not text:
            # Fallback: title-only fingerprint (skips n-gram if too short).
            text = (fr.title or "").strip()
        ngrams[url] = _ngram_set(text)

    # 2. Greedy pairwise scan, longest first.
    urls_sorted = sorted(
        url_to_meta.keys(),
        key=lambda u: url_to_meta[u].chars or 0,
        reverse=True,
    )
    removed = 0
    for i, url_i in enumerate(urls_sorted):
        if url_i not in url_to_meta:
            continue  # already evicted by an earlier iteration
        ng_i = ngrams.get(url_i, frozenset())
        if not ng_i:
            continue
        host_i = urlparse(url_i).netloc.lower()
        for url_j in urls_sorted[i + 1:]:
            if url_j not in url_to_meta:
                continue
            ng_j = ngrams.get(url_j, frozenset())
            if not ng_j:
                continue
            inter = len(ng_i & ng_j)
            union = len(ng_i | ng_j)
            if union == 0:
                continue
            jaccard = inter / union
            # Same-domain exemption: a site like ``anthropic.com`` has
            # shared chrome / branding / footer text across every page,
            # which inflates 5-gram Jaccard for genuinely different
            # pages (``/company`` vs ``/careers``). Require a much higher
            # threshold when hosts match to avoid false-positive drops.
            host_j = urlparse(url_j).netloc.lower()
            threshold = (
                _PARAPHRASE_SAME_DOMAIN_THRESHOLD
                if host_i and host_i == host_j
                else _PARAPHRASE_JACCARD_THRESHOLD
            )
            if jaccard > threshold:
                # Drop the shorter one (url_j). Sources + indices migrate
                # to the kept URL so the audit trail isn't lost.
                url_sources.setdefault(url_i, []).extend(
                    url_sources.pop(url_j, [])
                )
                url_query_indices.setdefault(url_i, []).extend(
                    url_query_indices.pop(url_j, [])
                )
                del url_to_meta[url_j]
                removed += 1
                same_dom = host_i == host_j
                safe_print(
                    f"[AGG] paraphrase dedup: dropped {url_j} "
                    f"(J={jaccard:.2f} with {url_i}"
                    f"{', same-domain' if same_dom else ''})"
                )
    return removed
