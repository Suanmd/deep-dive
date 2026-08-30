"""Regression tests for :mod:`deep_dive.crawler.engines.mmx`.

The most important test here is the regression for the
``AttributeError: 'function' object has no attribute '_uncached'``
that broke the first real deep-dive search against "山下有松 songmont"
(see ``CHANGELOG.md`` for the original incident).

If anyone ever refactors :func:`_resolve_mmx_path` again and re-introduces
the ``_uncached`` attribute lookup, this test will catch it.

.. note::

    ``monkeypatch`` is used (not ``with patch(...)``) so the mock
    remains active for the entire test. Earlier drafts scoped patches
    around ``MMXEngine(...)`` only, leaving ``engine.search(...)``
    outside the patch and accidentally invoking the real mmx CLI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from deep_dive.crawler.engines.base import (
    SearchEngineError,
    SearchEngineQuotaError,
    SearchEngineTimeoutError,
)
from deep_dive.crawler.engines.mmx import (
    MMXEngine,
    _MMX_PATH_CACHE,
    _resolve_mmx_path,
)


# ---------------------------------------------------------------------------
# Fixture: reset the module-level cache between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_mmx_cache():
    """Reset the path-resolution cache before/after each test."""
    import deep_dive.crawler.engines.mmx as mod
    mod._MMX_PATH_CACHE = False
    yield
    mod._MMX_PATH_CACHE = False


def _make_run_result(*, returncode: int = 0, stdout: str = '{"organic": []}', stderr: str = ""):
    """Build a mock CompletedProcess-like object for subprocess.run."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# _resolve_mmx_path
# ---------------------------------------------------------------------------

class TestResolveMmxPath:
    def test_resolves_when_mmx_on_path(self, monkeypatch):
        """shutil.which returns a path → cached and returned."""
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", lambda _: "/usr/bin/mmx")
        result = _resolve_mmx_path()
        assert result == "/usr/bin/mmx"

    def test_returns_none_when_mmx_missing(self, monkeypatch):
        """shutil.which returns None → cached and returned."""
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", lambda _: None)
        result = _resolve_mmx_path()
        assert result is None

    def test_caches_result_across_calls(self, monkeypatch):
        """Second call must NOT re-invoke shutil.which."""
        counter = {"n": 0}
        def fake_which(_):
            counter["n"] += 1
            return "/usr/bin/mmx"
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", fake_which)
        _resolve_mmx_path()
        _resolve_mmx_path()
        _resolve_mmx_path()
        assert counter["n"] == 1

    def test_caches_negative_result(self, monkeypatch):
        """shutil.which → None is also cached (don't re-check on every call)."""
        counter = {"n": 0}
        def fake_which(_):
            counter["n"] += 1
            return None
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", fake_which)
        _resolve_mmx_path()
        _resolve_mmx_path()
        _resolve_mmx_path()
        assert counter["n"] == 1

    def test_does_not_recheck_when_cached(self, monkeypatch):
        """Cached positive result must hold even if shutil.which is later patched to None."""
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", lambda _: "/usr/bin/mmx")
        _resolve_mmx_path()
        # Replace with a "shutil.which now returns None" mock. The cache
        # should make the function still return the original value.
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", lambda _: None)
        result = _resolve_mmx_path()
        assert result == "/usr/bin/mmx"

    def test_no_uncached_attribute_attribute_error(self, monkeypatch):
        """REGRESSION: ``_resolve_mmx_path()`` must NOT reference
        ``_resolve_mmx_path._uncached`` (which never existed). A
        previous implementation called ``_resolve_mmx_path._uncached()``
        on first invocation, raising::

            AttributeError: 'function' object has no attribute '_uncached'

        """
        monkeypatch.setattr("deep_dive.crawler.engines.mmx.shutil.which", lambda _: "/usr/bin/mmx")
        try:
            result = _resolve_mmx_path()
        except AttributeError as exc:
            pytest.fail(
                f"_resolve_mmx_path raised AttributeError: {exc}. "
                "The _uncached() method-on-function call regression has been re-introduced."
            )
        assert result == "/usr/bin/mmx"


# ---------------------------------------------------------------------------
# MMXEngine._raw_search — full behaviour
# ---------------------------------------------------------------------------

class TestMMXEngineSearch:
    def test_search_raises_error_when_mmx_missing(self, monkeypatch):
        """No ``mmx`` binary on PATH (single-credential pool) →
        ``SearchEngineError`` propagates so misconfiguration is
        obvious (a single-credential pool has nothing to rotate to,
        so the error must propagate rather than be absorbed into a
        rotation attempt).
        """
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: None)
        engine = MMXEngine(timeout_s=2.0)
        with pytest.raises(SearchEngineError):
            engine.search("test query", 10)

    def test_search_parses_valid_json(self, monkeypatch):
        """Normal mmx JSON response → list of SearchHit."""
        valid_json = '{"organic": [{"link": "https://a.com", "title": "A"}, {"link": "https://b.com", "title": "B"}]}'
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(stdout=valid_json),
        )
        engine = MMXEngine(timeout_s=2.0)
        hits = engine.search("test", 10)
        assert len(hits) == 2
        assert hits[0].url == "https://a.com"
        assert hits[0].title == "A"
        assert hits[0].engine == "mmx"

    def test_search_respects_topk(self, monkeypatch):
        """5 hits available, topk=3 → only 3 returned."""
        items = [{"link": f"https://{i}.com", "title": str(i)} for i in range(5)]
        import json as _json
        valid_json = _json.dumps({"organic": items})
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(stdout=valid_json),
        )
        engine = MMXEngine(timeout_s=2.0)
        hits = engine.search("test", 3)
        assert len(hits) == 3
        assert [h.url for h in hits] == ["https://0.com", "https://1.com", "https://2.com"]

    def test_search_raises_quota_on_quota_keyword(self, monkeypatch):
        """``mmx`` stderr contains ``exceeds your plan`` →
        ``SearchEngineQuotaError``.

        With only 1 credential, the rotation pool is exhausted and
        the quota error propagates. ``SearchEngineQuotaError`` is a
        subclass of ``SearchEngineError`` so the assertion matches.
        """
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(stderr="ERROR: exceeds your plan. try again later"),
        )
        engine = MMXEngine(timeout_s=2.0)
        with pytest.raises(SearchEngineQuotaError):
            engine.search("test", 5)

    def test_search_raises_error_on_nonzero_exit(self, monkeypatch):
        """``mmx`` returns a non-zero exit code →
        ``SearchEngineQuotaError``.

        Non-zero exit is treated as a credential-level failure so
        multi-credential pools can rotate. With only 1 credential
        the rotation exhausts and the exception propagates;
        ``SearchEngineQuotaError`` is a subclass of
        ``SearchEngineError`` so this assertion is compatible.
        """
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(returncode=1, stderr="some error"),
        )
        engine = MMXEngine(timeout_s=2.0)
        with pytest.raises(SearchEngineError):
            engine.search("test", 5)

    def test_search_raises_error_on_bad_json(self, monkeypatch):
        """mmx returns invalid JSON → SearchEngineError propagates (NOT rotated).

        Bad JSON is a permanent version-mismatch / corruption problem;
        rotating to another credential won't help, so this stays as a
        plain ``SearchEngineError`` (not in RETRYABLE_ERRORS) and
        propagates immediately.
        """
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(stdout="not valid json"),
        )
        engine = MMXEngine(timeout_s=2.0)
        with pytest.raises(SearchEngineError):
            engine.search("test", 5)

    def test_search_raises_quota_after_timeout_with_single_cred(self, monkeypatch):
        """Timeout with 1 credential → rotation pool exhausts →
        ``SearchEngineQuotaError``.

        Timeout is a retryable error (transient — a different binary
        might work), so :class:`MultiKeyEngine` marks the credential
        exhausted and tries the next. With only 1 credential the
        pool is fully exhausted and ``SearchEngineQuotaError``
        propagates.
        """
        import subprocess
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["mmx"], timeout=5)
            ),
        )
        engine = MMXEngine(timeout_s=2.0)
        with pytest.raises(SearchEngineQuotaError):
            engine.search("test", 5)

    def test_search_returns_empty_on_organic_missing(self, monkeypatch):
        """mmx JSON without 'organic' key → empty list (graceful)."""
        monkeypatch.setattr("deep_dive.crawler.engines.mmx._resolve_mmx_path", lambda: "/usr/bin/mmx")
        monkeypatch.setattr(
            "deep_dive.crawler.engines.mmx.subprocess.run",
            lambda *a, **kw: _make_run_result(stdout='{"results": []}'),
        )
        engine = MMXEngine(timeout_s=2.0)
        assert engine.search("test", 5) == []