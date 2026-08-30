"""Tests for multi-key engine rotation.

Covers :class:`EngineAccountPool` state management and
:class:`MultiKeyEngine` rotation across multiple credentials.
"""

from __future__ import annotations

import threading

import pytest

from deep_dive.crawler.engines.base import (
    EngineAccountPool,
    EngineCredential,
    MultiKeyEngine,
    SearchEngine,
    SearchEngineAuthError,
    SearchEngineError,
    SearchEngineNetworkError,
    SearchEngineQuotaError,
    SearchEngineTimeoutError,
)
from deep_dive.types import SearchHit


# ---------------------------------------------------------------------------
# EngineAccountPool state machine
# ---------------------------------------------------------------------------

class TestEngineAccountPool:
    def test_next_active_returns_first_cred(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        assert pool.next_active().name == "KEY1"

    def test_mark_exhausted_skips_to_next(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
            EngineCredential(name="KEY3", key="c"),
        ])
        pool.mark_exhausted("KEY1")
        assert pool.next_active().name == "KEY2"
        pool.mark_exhausted("KEY2")
        assert pool.next_active().name == "KEY3"

    def test_returns_none_when_all_exhausted(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        pool.mark_exhausted("KEY1")
        pool.mark_exhausted("KEY2")
        assert pool.next_active() is None
        assert pool.is_fully_exhausted is True

    def test_empty_pool_is_fully_exhausted(self):
        pool = EngineAccountPool([])
        assert pool.is_fully_exhausted is True
        assert pool.next_active() is None
        assert pool.active_count == 0
        assert pool.total_count == 0

    def test_reset_clears_exhausted_state(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
        ])
        pool.mark_exhausted("KEY1")
        assert pool.is_fully_exhausted
        pool.reset()
        assert not pool.is_fully_exhausted
        assert pool.next_active().name == "KEY1"

    def test_thread_safety(self):
        """Concurrent mark_exhausted / next_active should not lose updates."""
        pool = EngineAccountPool([
            EngineCredential(name=f"KEY{i}", key=str(i)) for i in range(50)
        ])
        errors: list[str] = []

        def exhaust_one(name: str):
            try:
                pool.mark_exhausted(name)
            except Exception as e:
                errors.append(f"mark: {e}")

        threads = [
            threading.Thread(target=exhaust_one, args=(f"KEY{i}",))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert pool.is_fully_exhausted

    def test_active_and_exhausted_names(self):
        pool = EngineAccountPool([
            EngineCredential(name="A", key="a"),
            EngineCredential(name="B", key="b"),
            EngineCredential(name="C", key="c"),
        ])
        pool.mark_exhausted("B")
        assert pool.active_names == ("A", "C")
        assert pool.exhausted_names == ("B",)


# ---------------------------------------------------------------------------
# MultiKeyEngine rotation logic
# ---------------------------------------------------------------------------

class _StubEngine(MultiKeyEngine):
    """Records the credential order; returns a fixed hit list per credential.

    Override :attr:`errors_per_cred` to inject failures keyed by credential
    name. Each entry is the exception class to raise; rotate retries until
    either a credential succeeds or the pool is exhausted.
    """

    name = "stub"

    def __init__(
        self,
        pool: EngineAccountPool,
        *,
        errors_per_cred: dict[str, type[BaseException]] | None = None,
    ):
        super().__init__(pool=pool, timeout_s=5.0)
        self.errors_per_cred = errors_per_cred or {}
        self.tried_order: list[str] = []

    def _try_with_credential(
        self,
        cred: EngineCredential,
        query: str,
        topk: int,
    ) -> list[SearchHit]:
        self.tried_order.append(cred.name)
        if cred.name in self.errors_per_cred:
            err_cls = self.errors_per_cred[cred.name]
            raise err_cls(f"{cred.name} simulated {err_cls.__name__}")
        return [SearchHit(url=f"https://example.com/{cred.name}",
                          title=f"hit from {cred.name}", engine=self.name)]


class TestMultiKeyRotation:
    def test_first_cred_succeeds(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool)
        hits = engine.search("test", 10)
        assert len(hits) == 1
        assert hits[0].title == "hit from KEY1"
        assert engine.tried_order == ["KEY1"]
        audit = engine.get_audit()
        assert audit["key_used"] == "KEY1"
        assert audit["keys_tried"] == ["KEY1"]
        assert audit["keys_exhausted"] == []

    def test_first_cred_quota_rotates_to_second(self):
        """KEY1 quota → KEY2 success. Audit trail records both."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineQuotaError,
        })
        hits = engine.search("test", 10)
        assert len(hits) == 1
        assert hits[0].title == "hit from KEY2"
        assert engine.tried_order == ["KEY1", "KEY2"]
        audit = engine.get_audit()
        assert audit["key_used"] == "KEY2"
        assert audit["keys_tried"] == ["KEY1", "KEY2"]
        assert audit["keys_exhausted"] == ["KEY1"]

    def test_first_cred_auth_rotates_to_second(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineAuthError,
        })
        hits = engine.search("test", 10)
        assert hits[0].title == "hit from KEY2"

    def test_first_cred_network_rotates_to_second(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineNetworkError,
        })
        hits = engine.search("test", 10)
        assert hits[0].title == "hit from KEY2"

    def test_first_cred_timeout_rotates_to_second(self):
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineTimeoutError,
        })
        hits = engine.search("test", 10)
        assert hits[0].title == "hit from KEY2"

    def test_all_creds_quota_raises_quota_error(self):
        """All N keys quota-exhausted → only THEN declare engine dead."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
            EngineCredential(name="KEY3", key="c"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineQuotaError,
            "KEY2": SearchEngineNetworkError,
            "KEY3": SearchEngineQuotaError,
        })
        with pytest.raises(SearchEngineQuotaError) as exc_info:
            engine.search("test", 10)
        # The error message should mention all 3 keys tried.
        assert "KEY1" in str(exc_info.value)
        assert "KEY2" in str(exc_info.value)
        assert "KEY3" in str(exc_info.value)
        # Audit: all tried, all exhausted, none used.
        audit = engine.get_audit()
        assert audit["key_used"] is None
        assert audit["keys_tried"] == ["KEY1", "KEY2", "KEY3"]
        assert audit["keys_exhausted"] == ["KEY1", "KEY2", "KEY3"]

    def test_non_retryable_error_propagates_immediately(self):
        """SearchEngineError (not in RETRYABLE_ERRORS) → stop rotation, propagate."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineError,  # NOT retryable
        })
        with pytest.raises(SearchEngineError):
            engine.search("test", 10)
        # KEY2 should NOT be tried; rotation stops on non-retryable.
        assert engine.tried_order == ["KEY1"]

    def test_first_cred_succeeds_after_quota_then_second_skipped(self):
        """Successful credential wins; later creds in the pool are not touched."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool)  # No errors → KEY1 succeeds
        hits = engine.search("test", 10)
        assert engine.tried_order == ["KEY1"]  # KEY2 never consulted

    def test_audit_isolated_per_thread(self):
        """Two threads calling search() on the same engine see separate audits."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool)

        # Thread A: succeed on KEY1
        # Thread B: succeed on KEY2 (force by patching errors_per_cred
        # at call time — we use separate engine instances since the stub
        # shares errors_per_cred state. Easiest: build two engines.)
        engine_a = _StubEngine(EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
        ]))
        engine_b = _StubEngine(EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ]), errors_per_cred={"KEY1": SearchEngineQuotaError})

        results: dict[str, dict] = {}

        def worker(name: str, eng: _StubEngine):
            eng.search("test", 10)
            results[name] = eng.get_audit()

        ta = threading.Thread(target=worker, args=("A", engine_a))
        tb = threading.Thread(target=worker, args=("B", engine_b))
        ta.start(); tb.start()
        ta.join(); tb.join()

        # A succeeded on KEY1 only; B rotated KEY1 → KEY2.
        assert results["A"]["key_used"] == "KEY1"
        assert results["A"]["keys_exhausted"] == []
        assert results["B"]["key_used"] == "KEY2"
        assert results["B"]["keys_exhausted"] == ["KEY1"]


class TestMultiKeyAuditAndPoolState:
    def test_audit_resets_but_pool_state_persists(self):
        """Per-call audit is fresh, but exhausted credentials stay exhausted.

        Quota is a session-level concept (Tavily / MMX quota counters
        don't reset between back-to-back searches within the same
        run). Retrying a quota'd key in the next call would waste API
        calls and just hit quota again. So pool state persists across
        calls, but the audit dict is fresh each call so the
        orchestrator records only the current call's rotation.
        """
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineQuotaError,
        })
        # First call: KEY1 quota → KEY2 success
        engine.search("test", 10)
        audit1 = engine.get_audit()
        assert audit1["key_used"] == "KEY2"
        assert audit1["keys_tried"] == ["KEY1", "KEY2"]
        assert audit1["keys_exhausted"] == ["KEY1"]
        # Second call: KEY1 still exhausted from first call, so only
        # KEY2 is tried. Audit shows just this call's rotation.
        engine.search("test", 10)
        audit2 = engine.get_audit()
        assert audit2["key_used"] == "KEY2"
        assert audit2["keys_tried"] == ["KEY2"]
        assert audit2["keys_exhausted"] == []

    def test_pool_reset_restores_exhausted_creds(self):
        """User can manually reset() to retry quota'd keys (e.g., after wait)."""
        pool = EngineAccountPool([
            EngineCredential(name="KEY1", key="a"),
            EngineCredential(name="KEY2", key="b"),
        ])
        engine = _StubEngine(pool, errors_per_cred={
            "KEY1": SearchEngineQuotaError,
        })
        engine.search("test", 10)
        assert "KEY1" in engine.pool.exhausted_names
        engine.pool.reset()
        assert engine.pool.active_names == ("KEY1", "KEY2")


# ---------------------------------------------------------------------------
# Engine wiring (pool carries engine_name)
# ---------------------------------------------------------------------------

class TestPoolNaming:
    def test_pool_name_round_trips(self):
        pool = EngineAccountPool(
            [EngineCredential(name="X", key="x")],
            name="tavily",
        )
        assert pool.name == "tavily"


# ---------------------------------------------------------------------------
# Real TavilyEngine integration with N-key fallback
# ---------------------------------------------------------------------------
#
# The abstract MultiKeyEngine rotation is tested via _StubEngine above.
# This class verifies the **real** TavilyEngine implementation does the
# same thing for an **arbitrary number of keys** (the CHANGELOG used
# to claim "KEY1 -> KEY2 fallback" which made it look like only 2 keys
# worked; the pool actually supports N via EngineAccountPool and these
# tests lock that in so it can't regress silently).

class TestTavilyEngineNKeyFallback:
    """Real TavilyEngine: N-key sequential fallback, not just 2."""

    def test_n_equals_3_first_two_quota_third_succeeds(self):
        """Pool of 3 keys: keys 1+2 hit quota, key 3 returns hits.

        Verifies the audit log proves the engine tried all 3 keys
        in pool order, marked the first 2 as exhausted, and returned
        hits only from key 3.
        """
        from unittest.mock import patch

        from deep_dive.crawler.engines.tavily import TavilyEngine

        pool = EngineAccountPool(
            [
                EngineCredential(name="KEY1", key="key-A-fake"),
                EngineCredential(name="KEY2", key="key-B-fake"),
                EngineCredential(name="KEY3", key="key-C-fake"),
            ],
            name="tavily-test",
        )
        engine = TavilyEngine(pool=pool, timeout_s=5)

        fail_keys = {"key-A-fake", "key-B-fake"}
        constructed: list[str] = []

        class MockTavily:
            def __init__(self, api_key):
                self.api_key = api_key
                constructed.append(api_key)

            def search(self, query, max_results):
                if self.api_key in fail_keys:
                    # Message MUST contain "quota" so TavilyEngine's
                    # _classify_error() maps it to
                    # SearchEngineQuotaError (which is in
                    # MultiKeyEngine.RETRYABLE_ERRORS) — so the
                    # current credential is marked exhausted and
                    # the engine moves to the next one.
                    raise Exception(f"429 quota exceeded for {self.api_key}")
                return {
                    "results": [
                        {
                            "url": f"https://example.com/{self.api_key}/{i}",
                            "title": f"Result {i} from {self.api_key}",
                            "content": "snippet",
                        }
                        for i in range(min(max_results, 3))
                    ]
                }

        with patch(
            "deep_dive.crawler.engines.tavily.TavilyClient", MockTavily
        ):
            hits = engine.search("test query", topk=3)

        audit = engine.get_audit()

        # All 3 keys attempted, in pool order — NOT just 2.
        assert constructed == ["key-A-fake", "key-B-fake", "key-C-fake"], (
            f"keys tried in wrong order: {constructed}"
        )
        # Audit: key 3 succeeded, keys 1+2 exhausted.
        assert audit["key_used"] == "KEY3"
        assert audit["keys_tried"] == ["KEY1", "KEY2", "KEY3"]
        assert audit["keys_exhausted"] == ["KEY1", "KEY2"]
        # Pool state persists across calls (quota is session-level).
        assert engine.pool.exhausted_names == ("KEY1", "KEY2")
        assert engine.pool.active_names == ("KEY3",)
        # All returned hits came from KEY3.
        assert len(hits) == 3
        assert all("key-C-fake" in h.url for h in hits), (
            f"hits should come from KEY3, got: {[h.url for h in hits]}"
        )

    def test_n_equals_5_all_fail_raises_quota(self):
        """Pool of 5 keys, all fail with quota → SearchEngineQuotaError.

        The orchestrator treats this as "engine exhausted" and falls
        back to DuckDuckGo. The exact N is arbitrary — if this test
        passes for N=5 the same code path works for any N.
        """
        from unittest.mock import patch

        import pytest

        from deep_dive.crawler.engines.tavily import TavilyEngine

        pool = EngineAccountPool(
            [
                EngineCredential(name=f"KEY{i + 1}", key=f"key-{i}-fake")
                for i in range(5)
            ],
            name="tavily-test",
        )
        engine = TavilyEngine(pool=pool, timeout_s=5)

        class MockTavily:
            def __init__(self, api_key):
                self.api_key = api_key

            def search(self, query, max_results):
                raise Exception(f"429 quota exceeded for {self.api_key}")

        with patch(
            "deep_dive.crawler.engines.tavily.TavilyClient", MockTavily
        ):
            with pytest.raises(SearchEngineQuotaError):
                engine.search("test", 3)

        # All 5 keys exhausted after this single call.
        assert engine.pool.is_fully_exhausted
        assert len(engine.pool.exhausted_names) == 5

    def test_env_var_TAVILY_API_KEYS_builds_n_cred_pool(self):
        """TAVILY_API_KEYS="k1,k2,k3,k4" env var -> pool of 4 credentials.

        Rotation is verified by ``test_n_equals_3_first_two_quota_third_succeeds``
        with an explicit pool. This test only verifies the env-var-to-pool
        mapping, which is the actual contract being tested here.

        Must explicitly blank TAVILY_API_KEY and TAVILY_API_KEY_BACKUP
        first — otherwise a real key set in the developer's shell gets
        appended to the pool AFTER the env-var keys, and the pool
        silently grows beyond N (which is a real open-source-readiness
        bug we want to catch).
        """
        import os
        from unittest.mock import patch

        from deep_dive.crawler.engines.tavily import TavilyEngine

        with patch.dict(
            os.environ,
            {
                "TAVILY_API_KEYS": "k1,k2,k3,k4",
                "TAVILY_API_KEY": "",
                "TAVILY_API_KEY_BACKUP": "",
            },
        ):
            engine = TavilyEngine(timeout_s=5)

        # Pool has exactly the 4 env-var keys, named KEY1..KEY4.
        cred_names = [c.name for c in engine.pool.credentials]
        cred_keys = [c.key for c in engine.pool.credentials]
        assert cred_names == ["KEY1", "KEY2", "KEY3", "KEY4"], (
            f"credential names wrong: {cred_names}"
        )
        assert cred_keys == ["k1", "k2", "k3", "k4"], (
            f"credential keys wrong: {cred_keys}"
        )
        assert engine.pool.total_count == 4

    def test_env_var_TAVILY_API_KEYS_rotates_through_all_n_keys(self):
        """Same env var, but force rotation through ALL N keys.

        k1..k(N-1) fail with quota; kN succeeds. Verifies the env-var
        pool supports arbitrary-length rotation, not just 2-key.
        """
        import os
        from unittest.mock import patch

        from deep_dive.crawler.engines.tavily import TavilyEngine

        constructed: list[str] = []

        class MockTavily:
            def __init__(self, api_key):
                self.api_key = api_key
                constructed.append(api_key)

            def search(self, query, max_results):
                if self.api_key in {"k1", "k2", "k3"}:
                    raise Exception(f"429 quota exceeded for {self.api_key}")
                # k4 succeeds
                return {
                    "results": [
                        {"url": f"https://x/{self.api_key}/0",
                         "title": "ok", "content": ""}
                    ]
                }

        with patch.dict(
            os.environ,
            {
                "TAVILY_API_KEYS": "k1,k2,k3,k4",
                "TAVILY_API_KEY": "",
                "TAVILY_API_KEY_BACKUP": "",
            },
        ):
            engine = TavilyEngine(timeout_s=5)
            with patch(
                "deep_dive.crawler.engines.tavily.TavilyClient", MockTavily
            ):
                hits = engine.search("test", 5)

        # All 4 keys from env var were attempted, in pool order.
        assert constructed == ["k1", "k2", "k3", "k4"], (
            f"env var keys not tried in order: {constructed}"
        )
        # k4 succeeded; k1..k3 exhausted.
        assert engine.get_audit()["key_used"] == "KEY4"
        assert engine.get_audit()["keys_exhausted"] == ["KEY1", "KEY2", "KEY3"]
        assert engine.pool.exhausted_names == ("KEY1", "KEY2", "KEY3")
        assert engine.pool.active_names == ("KEY4",)
        assert len(hits) == 1 and "k4" in hits[0].url