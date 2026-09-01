"""Cookie loader + URL matcher.

Reads the optional ``config/cookies.json`` and returns the cookies that
apply to a given URL.

File format (stable across versions for cookie portability)::

    {
      "zhihu": {
        "domain": ".zhihu.com",
        "cookies": [
          {"name": "...", "value": "...", "domain": ".zhihu.com", "path": "/"},
          ...
        ]
      },
      "baidu_wenku": {
        "domain": ".baidu.com",
        "cookies": [...]
      }
    }

Each top-level key is a "site config". :func:`match_cookies_to_url`
selects cookies whose ``domain`` field is a suffix of the URL's host.

This file is in :file:`.gitignore` (see project root) — never commit
real cookies.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from deep_dive.config import Config
from deep_dive.logging_setup import safe_print


@dataclass(slots=True, frozen=True)
class Cookie:
    """One browser cookie in Playwright-compatible shape."""

    name: str
    value: str
    domain: str
    path: str = "/"


def load_cookies(path: Path | None = None) -> dict[str, list[Cookie]]:
    """Load cookies from a JSON file.

    Args:
        path: explicit cookie file. If ``None``, falls back to
            :attr:`Config.cookie_file`.

    Returns:
        Mapping of ``site_key`` → ``list[Cookie]``. Empty dict if no
        file or file is unreadable.
    """
    if path is None:
        try:
            cfg = Config()
        except Exception:
            return {}
        path = cfg.cookie_file

    if path is None or not Path(path).exists():
        return {}

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        safe_print(f"[COOKIE] load failed: {type(e).__name__}: {e}")
        return {}

    out: dict[str, list[Cookie]] = {}
    for site_key, site_cfg in raw.items():
        # Skip non-dict entries defensively. The template
        # ``config/cookies.example.json`` uses ``_comment_*`` keys for
        # inline documentation; if a user copies the example file
        # without removing those keys, the loader would otherwise
        # crash with ``AttributeError: 'str' object has no
        # attribute 'get'``. This guards against schema additions /
        # metadata fields.
        if not isinstance(site_cfg, dict):
            continue
        cookies: list[Cookie] = []
        for c in site_cfg.get("cookies", []) or []:
            if not isinstance(c, dict):
                continue
            if not c.get("name") or c.get("value") is None:
                continue
            cookies.append(
                Cookie(
                    name=c["name"],
                    value=c["value"],
                    domain=c.get("domain", ""),
                    path=c.get("path", "/"),
                )
            )
        if cookies:
            out[site_key] = cookies
    return out


def match_cookies_to_url(
    url: str,
    cookies_map: Mapping[str, list[Cookie]],
) -> list[dict[str, str]]:
    """Find cookies that apply to ``url``.

    A cookie ``c`` applies if its ``domain`` (with leading dot stripped)
    is a substring of the URL's host **or** is a suffix of the URL's host::

        url = "https://www.zhihu.com/question/123"
        cookie.domain = ".zhihu.com"
        → match (zhihu.com is a suffix of www.zhihu.com)

    The legacy bug "inner loop break on first match → only first cookie
    per site" is fixed: we collect **all** matching cookies per site.

    Args:
        url: full URL to match against.
        cookies_map: output of :func:`load_cookies`.

    Returns:
        A list of cookie dicts in Playwright-compatible shape (the
        caller wraps them into ``context.add_cookies(...)``).
    """
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return []
    if not host:
        return []

    matched: list[dict[str, str]] = []
    for _site_key, cookies in cookies_map.items():
        for c in cookies:
            cdom = c.domain.lower().lstrip(".")
            if not cdom:
                continue
            if cdom in host or host.endswith(cdom):
                matched.append(
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or host,
                        "path": c.path,
                    }
                )
    return matched


def count_loaded(cookies_map: Mapping[str, Iterable[Cookie]]) -> tuple[int, int]:
    """Diagnostic helper: total cookies + sites with at least one cookie.

    Returns:
        (n_cookies, n_sites_with_cookies)
    """
    n_cookies = sum(len(v) for v in cookies_map.values() if isinstance(v, list))
    n_sites = sum(1 for v in cookies_map.values() if bool(v))
    return n_cookies, n_sites


__all__ = ["Cookie", "load_cookies", "match_cookies_to_url", "count_loaded"]
