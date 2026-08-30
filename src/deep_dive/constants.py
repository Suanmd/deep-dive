"""Project-wide constants.

Anything that used to be a module-level hardcoded constant in
``deep-dive`` lives here. Sub-modules should import from here, never
redefine. If you need to override a value at runtime, do it through
``Config`` rather than by editing this file.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Directory layout (relative to package root)
# ---------------------------------------------------------------------------

PACKAGE_NAME = "deep_dive"
DEFAULT_OUTPUT_DIRNAME = "tmp/deep-dive"
COOKIE_FILE_BASENAME = "cookies.json"
COOKIE_EXAMPLE_BASENAME = "cookies.example.json"
DEFAULTS_FILE_BASENAME = "defaults.yaml"

# ---------------------------------------------------------------------------
# Logging tags (replace emojis that break PowerShell GBK terminal)
# ---------------------------------------------------------------------------

TAG_OK = "[OK]"
TAG_ERR = "[ERR]"
TAG_WARN = "[WARN]"
TAG_INFO = "[INFO]"
TAG_FIRE = "[FIRE]"
TAG_TIME = "[TIME]"
TAG_DONE = "[DONE]"
TAG_HEARTBEAT = "[HEARTBEAT]"
TAG_TOOL = "[TOOL]"
TAG_STATS = "[STATS]"
TAG_RESCUE = "[AUTO-RESCUE]"
TAG_ENGINE_AUDIT = "[ENGINE-AUDIT]"
TAG_SMART_FILTER = "[SMART-FILTER]"
TAG_MMX_OK = "[MMX-OK]"
TAG_MMX_ERR = "[MMX-ERR]"
TAG_MMX_QUOTA = "[MMX-QUOTA]"
TAG_TAVILY_OK = "[TAVILY-OK]"
TAG_TAVILY_ERR = "[TAVILY-ERR]"
TAG_TAVILY_FALLBACK = "[TAVILY-FALLBACK]"
TAG_RELEVANCE = "[RELEVANCE]"
TAG_CFY = "[CAPY]"

# ---------------------------------------------------------------------------
# Timeouts / concurrency (defaults — Config can override)
# ---------------------------------------------------------------------------

DEFAULT_TASK_TIMEOUT_S = 240          # per-task subprocess timeout
DEFAULT_GLOBAL_TIMEOUT_S = 900         # watchdog for the whole run
DEFAULT_MAX_WORKERS = 2               # parallel task concurrency
DEFAULT_HEARTBEAT_INTERVAL_S = 10

# ---------------------------------------------------------------------------
# URL filtering — spam / blacklisted domains
# ---------------------------------------------------------------------------

# Aggregator pages, download sites, scraping farms, etc.
SPAM_DOMAINS: frozenset[str] = frozenset({
    "doc88.com", "dancihu.com", "browsenovel.com", "ningbojiahe.com",
    "youdao.com", "taobao.com", "jd.com", "amazon.com", "ebay.com",
    "facebook.com", "renrendoc.com", "25pp.com", "ibilibili.com",
})

# Cloudflare-protected sites that need the cloudscraper fallback.
CF_BLACK_DOMAINS: frozenset[str] = frozenset({
    "goodreads.com", "99csw.com", "book118.com", "weread.qq.com",
})

# Sites with bad signal-to-noise in search results.
LOWQ_DOMAINS: frozenset[str] = frozenset({
    "k73.com", "525566.com", "docin.com", "doc88.com", "25pp.com",
    "downza.cn", "crsky.com", "onlinedown.net", "mydown.yesky.com",
    "skycn.net", "zazhi.com.cn", "book118.com", "doc163.com",
    "csdn.net", "baike.baidu.com", "sohu.com",
    # AI tool / SEO aggregator sites. These publish short, near-identical
    # product descriptions rewritten from press releases or each other —
    # high noise ratio for research queries. Curated from observation
    # of ``deepseek mHC 架构`` / ``腾讯workbuddy`` runs (2026-08).
    "2090ai.com", "ai-bio.cn", "ai-bot.cn", "aiswill.com",
    "game773.com", "openi.cn", "xmsumi.com", "yumiok.com",
    # Anti-crawl / verification pages that return near-empty content.
    "toutiao.com",        # Toutiao (mobile + desktop) — 21-byte stubs
    "zhidao.baidu.com",   # Baidu Zhidao — security verification redirects
    "youku.com",          # Youku — video page, transcript not extractable
    "bilibili.com",       # Bilibili — video page, transcript not extractable
})

# Medium is mostly Cloudflare-blocked on Tavily → use alternatives.
MEDIUM_ALTERNATIVES: tuple[str, ...] = (
    "substack.com", "dev.to", "hackernoon.com", "towardsdatascience.com",
)

# ---------------------------------------------------------------------------
# Tracking parameters to strip during canonicalization
# ---------------------------------------------------------------------------

TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social", "utm_social-type",
    "gclid", "gclsrc", "gbraid", "wbraid", "fbclid", "msclkid",
    "yclid", "dclid", "spm", "ref", "ref_src", "ref_url", "referrer",
    "from", "source", "share_source", "share_medium",
})

# ---------------------------------------------------------------------------
# Blacklist patterns (compiled once at import time)
# ---------------------------------------------------------------------------

_BLACKLIST_PATH_PATTERNS = (
    re.compile(r"/(login|signup|register|signin|sign-in)\b", re.IGNORECASE),
    re.compile(r"/(cart|checkout|wishlist|account)\b", re.IGNORECASE),
    re.compile(r"/download/(free|trial|setup)", re.IGNORECASE),
    re.compile(r"/(ad|ads|advertisement)/", re.IGNORECASE),
    re.compile(r"[?&](sort|order|page|view)=\w+", re.IGNORECASE),
)

_LOWQ_HOST_PATTERNS = (
    re.compile(r"kongfz\.com/(item|shop)/\d+"),
    re.compile(r"weread\.qq\.com/web/reader/\w+"),
    re.compile(r"book118\.com/(p|view)/\d+\.html"),
    re.compile(r"renrendoc\.com/(doc|ppt)/\d+"),
)

# ---------------------------------------------------------------------------
# Cloudflare / WAF / challenge detection
# ---------------------------------------------------------------------------

BLOCK_KEYWORDS: tuple[str, ...] = (
    "just a moment", "verify you are human", "cloudflare", "captcha",
    "robot check", "security check", "ddos protection",
    "百度安全验证", "访问异常", "请进行验证", "安全验证",
    "请您登录后查看更多", "登录后查看更多", "知乎",
    "账号被封禁", "第三方修改过的浏览器", "微信读书",
    "雷池 WAF", "安全检测能力", "客户端异常", "合法用户",
)

# ---------------------------------------------------------------------------
# Quota detection keywords (used by engines and orchestrator)
# ---------------------------------------------------------------------------

QUOTA_KEYWORDS: tuple[str, ...] = (
    "exceeds your plan",
    "exceeded your plan",
    "plan's set usage limit",
    "plan usage limit",
    "quota exceeded",
    "quota_exceeded",
    "insufficient_quota",
    "rate_limit_exceeded",
    "rate limit exceeded",
    "rate-limit",
    "配额", "已达上限", "已达限制", "超出限制", "超出额度", "超限",
)

# ---------------------------------------------------------------------------
# User agents (rotated per request to avoid fingerprinting)
# ---------------------------------------------------------------------------

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)

VIEWPORTS: tuple[dict[str, int], ...] = (
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
)

# ---------------------------------------------------------------------------
# Relevance thresholds (single-char density + core-entity coverage)
# ---------------------------------------------------------------------------

QUERY_RELEVANCE_MIN_HITRATE = 0.25        # single-char density threshold
QUERY_CORE_ENTITY_MIN_HITRATE = 0.34      # core-entity coverage threshold
MIN_CHARS_DEFAULT = 1500                  # below this → low quality
                                          # to drop ~500-char aggregation / listing pages)

# ---------------------------------------------------------------------------
# Query variants — the 5 perspectives
# ---------------------------------------------------------------------------

VARIANT_KEYS: tuple[str, ...] = (
    "original", "refined", "critique", "academic", "primary",
    "comparative", "en_query", "en_variant", "en_academic",
)

# ---------------------------------------------------------------------------
# Stopwords for the capy summary keyword extraction
# ---------------------------------------------------------------------------

CN_STOPWORDS: frozenset[str] = frozenset({
    "的", "是", "在", "了", "和", "与", "或", "及", "为", "以",
    "上", "下", "中", "内",
})

EN_STOPWORDS: frozenset[str] = frozenset({
    "this", "that", "with", "from", "have", "been", "will", "they", "their",
    "about", "which", "what", "when", "where", "there", "here", "more",
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her",
    "was",
})

# ---------------------------------------------------------------------------
# Capy summary — themed clustering keywords
# ---------------------------------------------------------------------------

PREDICTIVE_KEYWORDS: tuple[str, ...] = (
    "预测", "目标价", "预计", "预期",
    "target", "forecast", "predict", "estimate", "outlook", "展望",
)
BULL_KEYWORDS: tuple[str, ...] = (
    "看涨", "上涨", "看多", "利好", "支撑", "上行", "上涨动能", "突破",
    "创新高", "高盛", "摩根", "买入", "增持",
    "bullish", "rally", "surge", "上行趋势",
)
BEAR_KEYWORDS: tuple[str, ...] = (
    "看跌", "下跌", "看空", "利空", "下行", "回调", "回落", "风险",
    "暴跌", "下行风险", "顶部", "阻力",
    "bearish", "decline", "fall", "drop", "correction", "warning",
)


def public() -> dict[str, object]:
    """Return a snapshot of public constants (for ``--print-config``)."""
    return {
        "version": __version__,
        "default_output_dirname": DEFAULT_OUTPUT_DIRNAME,
        "task_timeout_s": DEFAULT_TASK_TIMEOUT_S,
        "global_timeout_s": DEFAULT_GLOBAL_TIMEOUT_S,
        "max_workers": DEFAULT_MAX_WORKERS,
        "min_chars": MIN_CHARS_DEFAULT,
        "relevance_min_hitrate": QUERY_RELEVANCE_MIN_HITRATE,
        "core_entity_min_hitrate": QUERY_CORE_ENTITY_MIN_HITRATE,
        "n_spam_domains": len(SPAM_DOMAINS),
        "n_lowq_domains": len(LOWQ_DOMAINS),
        "n_cf_black_domains": len(CF_BLACK_DOMAINS),
        "n_tracking_params": len(TRACKING_PARAMS),
    }


__all__ = [
    "__version__",
    "PACKAGE_NAME",
    "DEFAULT_OUTPUT_DIRNAME",
    "COOKIE_FILE_BASENAME",
    "COOKIE_EXAMPLE_BASENAME",
    "DEFAULTS_FILE_BASENAME",
    # Logging tags
    "TAG_OK", "TAG_ERR", "TAG_WARN", "TAG_INFO", "TAG_FIRE", "TAG_TIME",
    "TAG_DONE", "TAG_HEARTBEAT", "TAG_TOOL", "TAG_STATS", "TAG_RESCUE",
    "TAG_ENGINE_AUDIT", "TAG_SMART_FILTER", "TAG_MMX_OK", "TAG_MMX_ERR",
    "TAG_MMX_QUOTA", "TAG_TAVILY_OK", "TAG_TAVILY_ERR", "TAG_TAVILY_FALLBACK",
    "TAG_RELEVANCE", "TAG_CFY",
    # Timeouts
    "DEFAULT_TASK_TIMEOUT_S", "DEFAULT_GLOBAL_TIMEOUT_S",
    "DEFAULT_MAX_WORKERS", "DEFAULT_HEARTBEAT_INTERVAL_S",
    # Domains & filters
    "SPAM_DOMAINS", "CF_BLACK_DOMAINS", "LOWQ_DOMAINS", "MEDIUM_ALTERNATIVES",
    "TRACKING_PARAMS",
    "_BLACKLIST_PATH_PATTERNS", "_LOWQ_HOST_PATTERNS",
    "BLOCK_KEYWORDS", "QUOTA_KEYWORDS",
    "USER_AGENTS", "VIEWPORTS",
    # Relevance
    "QUERY_RELEVANCE_MIN_HITRATE", "QUERY_CORE_ENTITY_MIN_HITRATE",
    "MIN_CHARS_DEFAULT", "VARIANT_KEYS",
    "CN_STOPWORDS", "EN_STOPWORDS",
    "PREDICTIVE_KEYWORDS", "BULL_KEYWORDS", "BEAR_KEYWORDS",
    "public",
]
