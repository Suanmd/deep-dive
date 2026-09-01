"""Domain / path blacklists used during crawl and search.

Grouped here so the aggregator (topic-level stats) and the reporting
layer (for "low quality" labels) can both reuse them.

Set membership is a **substring** match on the URL host:

    "goodreads.com" in "www.goodreads.com"       → True   (matches)
    "goodreads.com" in "goodreads-clone.io"      → False  (does not match)

If you want stricter exact-host matching, change ``_host_match`` below.
"""

from __future__ import annotations

from urllib.parse import urlparse

from deep_dive.constants import CF_BLACK_DOMAINS, LOWQ_DOMAINS, SPAM_DOMAINS

# Baidu sub-domains that need the special "warm-up + click search box"
# bypass in Playwright.
BAIDU_DOMAINS: frozenset[str] = frozenset(
    {
        "baike.baidu.com",
        "wapbaike.baidu.com",
        "baike.baidu.com.hk",
        "zhidao.baidu.com",
        "zhuanlan.baidu.com",
        "baijiahao.baidu.com",
        "xuewen.baidu.com",
        "v.baidu.com",
        "image.baidu.com",
        "baidu.com/s?",
        "www.baidu.com/s",
    }
)


def _host_match(url: str, needles: frozenset[str]) -> bool:
    """Return True if any of ``needles`` is a substring of the URL's host.

    Args:
        url: full URL.
        needles: collection of substrings to look for.

    Returns:
        True if any needle appears in the host portion of the URL.
    """
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    return any(n in host for n in needles)


def is_black_domain(url: str) -> bool:
    """True if URL is in the Cloudflare-protected blacklist."""
    return _host_match(url, CF_BLACK_DOMAINS)


def is_spam_domain(url: str) -> bool:
    """True if URL is in the spam-domain blacklist."""
    return _host_match(url, SPAM_DOMAINS)


def is_lowq_domain(url: str) -> bool:
    """True if URL is in the low-quality-domain blacklist."""
    return _host_match(url, LOWQ_DOMAINS)


def is_baidu_domain(url: str) -> bool:
    """True if URL is a Baidu property (needs warm-up bypass)."""
    try:
        host = urlparse(url).netloc.lower()
        path = urlparse(url).path.lower()
    except Exception:
        return False
    full = host + path
    return any(b in full for b in BAIDU_DOMAINS)


__all__ = [
    "BAIDU_DOMAINS",
    "is_black_domain",
    "is_baidu_domain",
    "is_lowq_domain",
    "is_spam_domain",
]
