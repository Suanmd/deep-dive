"""Tests for query variant generation."""

from __future__ import annotations

from deep_dive.query_variants import has_variant


class TestHasVariant:
    def test_present_and_nonempty(self):
        v = {"a": "value", "b": ""}
        assert has_variant(v, "a") is True

    def test_empty_value(self):
        v = {"a": ""}
        assert has_variant(v, "a") is False

    def test_missing_key(self):
        v = {}
        assert has_variant(v, "a") is False
