"""Tests for URL canonicalization.

Validates byte-for-byte parity with the legacy
``_canonicalize_url`` helper used in the previous
``deep-search`` crawler implementation.
"""

from __future__ import annotations

import pytest

from deep_dive.filters.canonical import (
    canonicalize_url,
    sort_query_params,
    strip_tracking,
)


class TestStripTracking:
    def test_strips_utm_params(self):
        result = strip_tracking("utm_source=x&utm_medium=y&keep=1")
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "keep=1" in result

    def test_sorts_remaining_params(self):
        result = strip_tracking("z=1&a=2&m=3")
        # Sorted alphabetically
        assert result == "a=2&m=3&z=1"

    def test_empty_query(self):
        assert strip_tracking("") == ""

    def test_custom_tracking_params(self):
        result = strip_tracking("foo=1&bar=2", params={"foo"})
        assert "foo" not in result
        assert "bar=2" in result

    def test_preserves_blank_values(self):
        result = strip_tracking("a=&b=2")
        # a= should remain (keep_blank_values=True)
        assert "a=" in result
        assert "b=2" in result


class TestSortQueryParams:
    def test_sorts_alphabetically(self):
        assert sort_query_params("z=1&a=2&m=3") == "a=2&m=3&z=1"

    def test_already_sorted(self):
        assert sort_query_params("a=1&b=2") == "a=1&b=2"

    def test_empty(self):
        assert sort_query_params("") == ""

    def test_single_param(self):
        assert sort_query_params("only=1") == "only=1"


class TestCanonicalizeUrl:
    def test_lowercases_host(self):
        assert canonicalize_url("HTTPS://Example.COM/path") == "https://example.com/path"

    def test_strips_trailing_slash(self):
        assert canonicalize_url("https://example.com/a/") == "https://example.com/a"
        # Root path is preserved
        assert canonicalize_url("https://example.com/") == "https://example.com/"

    def test_strips_tracking_params(self):
        result = canonicalize_url(
            "https://example.com/path?utm_source=x&keep=1"
        )
        assert "utm_source" not in result
        assert "keep=1" in result

    def test_sorts_remaining_query_params(self):
        result = canonicalize_url("https://example.com/p?z=1&a=2")
        assert result.endswith("?a=2&z=1")

    def test_drops_fragment(self):
        assert canonicalize_url("https://example.com/p#section") == "https://example.com/p"

    def test_preserves_path_case(self):
        # Path case is preserved (legacy behavior — only host is lowercased)
        assert canonicalize_url("https://Example.COM/CamelCase") == "https://example.com/CamelCase"

    def test_invalid_url_returned_as_is(self):
        # Not a real URL → returned unchanged
        assert canonicalize_url("not a url") == "not a url"

    def test_empty_url_returned_as_is(self):
        assert canonicalize_url("") == ""

    @pytest.mark.parametrize(
        "url_in,url_out",
        [
            ("HTTPS://Example.COM/a/?utm_source=x&id=1#frag",
             "https://example.com/a?id=1"),
            ("https://example.com/p?gclid=ABC&id=5",
             "https://example.com/p?id=5"),
            ("https://api.example.com/v1?b=2&a=1",
             "https://api.example.com/v1?a=1&b=2"),
        ],
    )
    def test_combo(self, url_in, url_out):
        assert canonicalize_url(url_in) == url_out
