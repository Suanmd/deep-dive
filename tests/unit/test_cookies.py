"""Tests for :mod:`deep_dive.crawler.cookies`.

Covers the defensive loader that skips non-dict entries in the JSON
file. Previously the loader crashed with ``AttributeError: 'str'
object has no attribute 'get'`` when top-level keys held strings
(e.g. the ``_comment_*`` keys in
:file:`config/cookies.example.json`).
"""

from __future__ import annotations

import json

import pytest

from deep_dive.crawler.cookies import (
    Cookie,
    count_loaded,
    load_cookies,
    match_cookies_to_url,
)


@pytest.fixture
def comment_and_valid_mixed(tmp_path):
    """A cookies.json that mixes ``_comment_*`` keys (string values)
    with real site configs. Mirrors the template example.json pattern.
    """
    payload = {
        "_comment_top": "============================================",
        "_comment_2": "Cookie configuration",
        "_comment_3": "============================================",
        "zhihu": {
            "domain": ".zhihu.com",
            "cookies": [
                {"name": "z_c0", "value": "fake-z-c0", "domain": ".zhihu.com", "path": "/"},
                {"name": "d_c0", "value": "fake-d-c0", "domain": ".zhihu.com", "path": "/"},
            ],
        },
        "_meta": {
            "last_updated": "2026-08-31",
            "owner": "throwaway account",
        },
        "baidu_wenku": {
            "domain": ".baidu.com",
            "cookies": [
                {"name": "BDUSS", "value": "fake-bduss", "domain": ".baidu.com", "path": "/"},
            ],
        },
    }
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


class TestLoadCookiesDefensive:
    """Loader skips non-dict ``site_cfg`` entries."""

    def test_string_valued_keys_dont_crash(self, comment_and_valid_mixed):
        """``_comment_top`` etc. are strings. The loader skips them
        instead of crashing with ``AttributeError`` on ``.get``."""
        result = load_cookies(comment_and_valid_mixed)
        assert isinstance(result, dict)
        assert "zhihu" in result
        assert "baidu_wenku" in result
        # String-valued entries must be filtered out (not in result keys).
        assert "_comment_top" not in result
        assert "_meta" not in result

    def test_loads_real_cookies_from_mixed_file(self, comment_and_valid_mixed):
        """Real cookies are loaded even when metadata keys coexist."""
        result = load_cookies(comment_and_valid_mixed)
        n_cookies, n_sites = count_loaded(result)
        assert n_sites == 2
        assert n_cookies == 3  # 2 zhihu + 1 baidu

    def test_user_single_comment_key_works(self, tmp_path):
        """Regression guard: a user file with a single ``_comment``
        key (common pattern) must not crash the loader.
        """
        payload = {
            "_comment": "My throwaway zhihu account cookies.",
            "zhihu": {
                "domain": ".zhihu.com",
                "cookies": [
                    {"name": "z_c0", "value": "abc", "domain": ".zhihu.com", "path": "/"},
                ],
            },
        }
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        result = load_cookies(p)
        assert "zhihu" in result
        assert result["zhihu"][0].name == "z_c0"


class TestLoadCookiesBasics:
    """Sanity tests for the cookie loader."""

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        from deep_dive.config import Config

        monkeypatch.setattr(Config, "cookie_file", tmp_path / "nonexistent.json")
        assert load_cookies() == {}

    def test_empty_cookies_array_skipped(self, tmp_path):
        """Site config with empty cookies array → site not in result."""
        payload = {
            "zhihu": {"domain": ".zhihu.com", "cookies": []},
            "baidu": {
                "domain": ".baidu.com",
                "cookies": [{"name": "BDUSS", "value": "x", "domain": ".baidu.com", "path": "/"}],
            },
        }
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        result = load_cookies(p)
        assert "zhihu" not in result  # empty array → site skipped
        assert "baidu" in result

    def test_invalid_cookie_entries_skipped(self, tmp_path):
        """Cookies missing name or value → skipped."""
        payload = {
            "zhihu": {
                "domain": ".zhihu.com",
                "cookies": [
                    {"name": "z_c0", "value": "valid", "domain": ".zhihu.com"},
                    {"name": "no_value", "value": None},  # invalid
                    {"value": "no_name"},  # invalid
                    {"name": "d_c0", "value": "valid2", "domain": ".zhihu.com"},
                ],
            },
        }
        p = tmp_path / "cookies.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        result = load_cookies(p)
        names = [c.name for c in result["zhihu"]]
        assert "z_c0" in names
        assert "d_c0" in names
        assert "no_value" not in names
        assert "no_name" not in names


class TestMatchCookiesToUrl:
    """URL matching basic coverage."""

    def test_zhihu_cookie_matches_zhihu_urls(self):
        cookies_map = {
            "zhihu": [Cookie(name="z_c0", value="abc", domain=".zhihu.com", path="/")],
        }
        matched = match_cookies_to_url("https://www.zhihu.com/question/123", cookies_map)
        assert any(c["name"] == "z_c0" for c in matched)

    def test_zhihu_cookie_does_not_match_baidu(self):
        cookies_map = {
            "zhihu": [Cookie(name="z_c0", value="abc", domain=".zhihu.com", path="/")],
        }
        matched = match_cookies_to_url("https://www.baidu.com/", cookies_map)
        assert matched == []
