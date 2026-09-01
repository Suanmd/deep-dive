"""Project-wide constants.

Anything that used to be a module-level hardcoded constant in
``deep-dive`` lives here. Sub-modules should import from here, never
redefine. If you need to override a value at runtime, do it through
``Config`` rather than by editing this file.
"""

from __future__ import annotations

import re
from typing import Any

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

DEFAULT_TASK_TIMEOUT_S = 240  # per-task subprocess timeout
DEFAULT_GLOBAL_TIMEOUT_S = 900  # watchdog for the whole run
DEFAULT_MAX_WORKERS = 2  # parallel task concurrency
DEFAULT_HEARTBEAT_INTERVAL_S = 10

# ---------------------------------------------------------------------------
# URL filtering — spam / blacklisted domains
# ---------------------------------------------------------------------------

# Aggregator pages, download sites, scraping farms, etc.
SPAM_DOMAINS: frozenset[str] = frozenset(
    {
        "doc88.com",
        "dancihu.com",
        "browsenovel.com",
        "ningbojiahe.com",
        "youdao.com",
        "taobao.com",
        "jd.com",
        "amazon.com",
        "ebay.com",
        "facebook.com",
        "renrendoc.com",
        "25pp.com",
        "ibilibili.com",
    }
)

# Cloudflare-protected sites that need the cloudscraper fallback.
CF_BLACK_DOMAINS: frozenset[str] = frozenset(
    {
        "goodreads.com",
        "99csw.com",
        "book118.com",
        "weread.qq.com",
    }
)

# Sites with bad signal-to-noise in search results.
LOWQ_DOMAINS: frozenset[str] = frozenset(
    {
        "k73.com",
        "525566.com",
        "docin.com",
        "doc88.com",
        "25pp.com",
        "downza.cn",
        "crsky.com",
        "onlinedown.net",
        "mydown.yesky.com",
        "skycn.net",
        "zazhi.com.cn",
        "book118.com",
        "doc163.com",
        "csdn.net",
        "baike.baidu.com",
        "sohu.com",
        "2090ai.com",
        "ai-bio.cn",
        "ai-bot.cn",
        "aiswill.com",
        "game773.com",
        "openi.cn",
        "xmsumi.com",
        "yumiok.com",
        "toutiao.com",
        "zhidao.baidu.com",
        "youku.com",
        "bilibili.com",
    }
)

# Music / media platforms whose detail pages are short but legitimate.
# Without this exemption, queries like "song title" lose 50-60% of corpus
# to the `min_chars=1500` gate (v3 regression on 红尘客栈 歌曲 showed
# 22/37 = 60% flagged low_chars). These domains are authoritative for
# music info even when individual pages are <1000 chars.
MUSIC_DOMAIN_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "music.apple.com",
        "kuwo.cn",
        "kugou.com",
        "music.163.com",  # NetEase Cloud Music
        "y.qq.com",  # QQ Music
        "xiami.com",
        "music.126.net",
        "spotify.com",
        "soundcloud.com",
        "bandcamp.com",
        "genius.com",  # lyrics database
        "lyrics.net",
        "azlyrics.com",
        "musixmatch.com",
        "moegirl.org",  # Chinese media wiki (anime / music metadata)
        "vgmdb.net",  # Video game music database
    }
)

# Site targets for music/media queries.
# Applied when kind=general AND the query contains music-related
# keywords (歌 / 歌曲 / 音乐 / 专辑 / 单曲 / lyrics / song / album).
MUSIC_TARGET_SITES: tuple[str, ...] = (
    "music.163.com",
    "y.qq.com",
    "music.apple.com",
    "kuwo.cn",
    "kugou.com",
    "spotify.com",
)

# Music-query keyword triggers (zh + en). When ANY of these appears in a
# general-kind query, the auto_plan adds MUSIC_TARGET_SITES to the
# matrix. Keeps existing kind=music detection aligned with what users
# actually type (no formal `kind=music` enum yet).
# NOTE: no leading underscore — Python 3.14's import machinery has a
# quirky behavior where leading-underscore names can be in dir() but
# fail `from module import _name` (probably an interaction with the
# `from __future__ import annotations` in this file).
MUSIC_QUERY_TRIGGERS_ZH: frozenset[str] = frozenset(
    {"歌", "歌曲", "音乐", "专辑", "单曲", "歌词", "歌手", "歌曲名", "曲目"}
)
MUSIC_QUERY_TRIGGERS_EN: frozenset[str] = frozenset(
    {"song", "lyrics", "album", "single", "ep", "discography", "music"}
)

# Binary content URLs that trafilatura can't extract cleanly. PDFs in
# particular produce high-char-count "text" that is actually compressed
# binary decoded as UTF-8 (lots of `??` / `锟斤拷` style garbage), which
# then dominates the info-density score because the char count is huge
# even though the actual sentence density is near zero. Skip these at
# the pipeline level.
BINARY_CONTENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".exe",
        ".dmg",
        ".iso",
        ".dll",
        ".so",
    }
)

# Medium is mostly Cloudflare-blocked on Tavily → use alternatives.
MEDIUM_ALTERNATIVES: tuple[str, ...] = (
    "substack.com",
    "dev.to",
    "hackernoon.com",
    "towardsdatascience.com",
)

# ---------------------------------------------------------------------------
# Tracking parameters to strip during canonicalization
# ---------------------------------------------------------------------------

TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        "utm_brand",
        "utm_social",
        "utm_social-type",
        "gclid",
        "gclsrc",
        "gbraid",
        "wbraid",
        "fbclid",
        "msclkid",
        "yclid",
        "dclid",
        "spm",
        "ref",
        "ref_src",
        "ref_url",
        "referrer",
        "from",
        "source",
        "share_source",
        "share_medium",
    }
)

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
    "just a moment",
    "verify you are human",
    "cloudflare",
    "captcha",
    "robot check",
    "security check",
    "ddos protection",
    "百度安全验证",
    "访问异常",
    "请进行验证",
    "安全验证",
    "请您登录后查看更多",
    "登录后查看更多",
    "知乎",
    "账号被封禁",
    "第三方修改过的浏览器",
    "微信读书",
    "雷池 WAF",
    "安全检测能力",
    "客户端异常",
    "合法用户",
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
    "配额",
    "已达上限",
    "已达限制",
    "超出限制",
    "超出额度",
    "超限",
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

QUERY_RELEVANCE_MIN_HITRATE = 0.25  # single-char density threshold
QUERY_CORE_ENTITY_MIN_HITRATE = 0.34  # core-entity coverage threshold
MIN_CHARS_DEFAULT = 1500  # below this → low quality
# to drop ~500-char aggregation / listing pages)

# ---------------------------------------------------------------------------
# Query variants — the 5 perspectives
# ---------------------------------------------------------------------------

VARIANT_KEYS: tuple[str, ...] = (
    "original",
    "refined",
    "critique",
    "academic",
    "primary",
    "comparative",
    "en_query",
    "en_variant",
    "en_academic",
)

# ---------------------------------------------------------------------------
# Stopwords for the capy summary keyword extraction
# ---------------------------------------------------------------------------

CN_STOPWORDS: frozenset[str] = frozenset(
    {
        "的",
        "是",
        "在",
        "了",
        "和",
        "与",
        "或",
        "及",
        "为",
        "以",
        "上",
        "下",
        "中",
        "内",
    }
)

EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "will",
        "they",
        "their",
        "about",
        "which",
        "what",
        "when",
        "where",
        "there",
        "here",
        "more",
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "her",
        "was",
    }
)

# ---------------------------------------------------------------------------
# Capy summary — themed clustering keywords
# ---------------------------------------------------------------------------

PREDICTIVE_KEYWORDS: tuple[str, ...] = (
    "预测",
    "目标价",
    "预计",
    "预期",
    "target",
    "forecast",
    "predict",
    "estimate",
    "outlook",
    "展望",
)
BULL_KEYWORDS: tuple[str, ...] = (
    "看涨",
    "上涨",
    "看多",
    "利好",
    "支撑",
    "上行",
    "上涨动能",
    "突破",
    "创新高",
    "高盛",
    "摩根",
    "买入",
    "增持",
    "bullish",
    "rally",
    "surge",
    "上行趋势",
)
BEAR_KEYWORDS: tuple[str, ...] = (
    "看跌",
    "下跌",
    "看空",
    "利空",
    "下行",
    "回调",
    "回落",
    "风险",
    "暴跌",
    "下行风险",
    "顶部",
    "阻力",
    "bearish",
    "decline",
    "fall",
    "drop",
    "correction",
    "warning",
)


# ---------------------------------------------------------------------------
# Info-density scoring (replaces pure char-count threshold)
# ---------------------------------------------------------------------------
# Rationale: a 250-char news brief that says "TikTok 2024 营收 230 亿美元,
# 同比+43%" carries more analytical value than a 5000-char opinion piece
# that never names a number. The previous ``min_chars`` gate discarded
# 21/44 = 48% of URLs in a real run, including data-dense short articles.
# The fix: a two-axis score (data points + proper nouns) per 1k chars,
# combined with a hard char-count floor so a 50-char snippet never
# sneaks in via density alone.

_DIGIT_RE = re.compile(r"\d+(?:[.,]\d+)?")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
_MONEY_RE = re.compile(
    r"(?:¥|￥|\$|€|£|USD|CNY|HKD|TWD)\s*\d+(?:[.,]\d+)*"
    r"|\d+(?:[.,]\d+)*\s*(?:亿|万亿|百万|千万|万|billion|million|thousand|bn|mn|bn\.)",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|美元|元|港元|新台币|bps|倍|MAU|DAU|GMV|营收|收入|份额|增速|市值)",
    re.IGNORECASE,
)
_PROPER_NOUN_RE = re.compile(r"(?<![A-Za-z])[A-Z][a-zA-Z]{2,}")


def info_density_score(text: str) -> dict[str, Any]:
    """Compute an information-density score for a piece of text.

    Two axes:

    1. **Data points** — numbers, %, currency, units. Each occurrence
       counts once. Money/percent get a 2x weight because they encode
       economic reality directly.
    2. **Proper nouns** — capitalised words (rough proxy for entity
       names: companies, products, places).

    Combined score is clamped to 0-100. The empirical sweet spot
    observed in business / tech research: a 1000-char Chinese article
    with 5 numbers + 3 proper nouns scores ~30; the same article with
    only 1 number scores ~8.

    Returns:
        Dict with keys: ``char_count``, ``digit_count``, ``money_count``,
        ``percent_count``, ``proper_noun_count``, ``data_points``,
        ``data_density_per_1k``, ``proper_density_per_1k``, ``score``.
    """
    if not text:
        return {
            "char_count": 0,
            "digit_count": 0,
            "money_count": 0,
            "percent_count": 0,
            "proper_noun_count": 0,
            "data_points": 0,
            "data_density_per_1k": 0.0,
            "proper_density_per_1k": 0.0,
            "score": 0,
        }
    digits = len(_DIGIT_RE.findall(text))
    money = len(_MONEY_RE.findall(text))
    percent = len(_PERCENT_RE.findall(text))
    units = len(_UNIT_RE.findall(text))
    proper = len(_PROPER_NOUN_RE.findall(text))
    char_count = len(text)
    data_points = digits + 2 * money + 2 * percent + units
    data_density_per_1k = (data_points / max(char_count, 1)) * 1000
    proper_density_per_1k = (proper / max(char_count, 1)) * 1000
    # Weights tuned on 5 business-research runs; data_density matters
    # more than proper nouns because numbers carry semantic load.
    raw_score = data_density_per_1k * 1.5 + proper_density_per_1k * 0.8
    score = max(0, min(100, int(raw_score)))
    return {
        "char_count": char_count,
        "digit_count": digits,
        "money_count": money,
        "percent_count": percent,
        "proper_noun_count": proper,
        "data_points": data_points,
        "data_density_per_1k": round(data_density_per_1k, 2),
        "proper_density_per_1k": round(proper_density_per_1k, 2),
        "score": score,
    }


# Density thresholds. Picked so a 250-char Chinese article with 3 numbers
# and 1 currency mention clears the bar (score ~30+), while a 5000-char
# pure narrative without numbers falls below it (score <10).
DENSITY_SCORE_THRESHOLD = 25  # below → low-quality even if chars ok
# Hard floor for char count. Below this, the article is almost always
# a footer / navigation fragment, NOT a data-dense brief — even with
# a number it would still be missing context. 80 chars is roughly the
# length of a Chinese short headline ("TikTok 2024 营收 230 亿美元
# (+43%)") so legitimate data-dense briefs can clear it. A 50-char
# footer fragment will not.
MIN_CHARS_HARD_FLOOR = 80


def is_low_quality(text: str, min_chars: int = MIN_CHARS_DEFAULT, url: str = "") -> tuple[bool, str]:
    """Decide whether ``text`` should be treated as low-quality.

    Args:
        text: the article body.
        min_chars: legacy char-count threshold (default :data:`MIN_CHARS_DEFAULT`).
        url: optional source URL — if it matches :data:`MUSIC_DOMAIN_EXEMPTIONS`,
            short content is allowed (a song detail page is typically
            400-1000 chars and that's fine). Without this exemption
            music queries lose 50-60% of corpus to ``min_chars`` gate.

    Returns:
        ``(is_lowq, reason)`` — reason is one of:
        - ``"too_short"`` — below :data:`MIN_CHARS_HARD_FLOOR`
        - ``"low_chars"`` — chars below min_chars AND density too low
        - ``"low_density"`` — chars OK but density below threshold
        - ``""`` (empty) — quality OK
    """
    # Music / media domain exemption
    if url:
        url_lower = url.lower()
        for domain in MUSIC_DOMAIN_EXEMPTIONS:
            if domain in url_lower:
                if not text:
                    return True, "too_short"
                if len(text) < 100:
                    return True, "too_short"
                return False, ""

    if not text:
        return True, "too_short"
    char_count = len(text)
    if char_count < MIN_CHARS_HARD_FLOOR:
        return True, "too_short"
    if char_count >= min_chars:
        # Char count already passes the legacy bar — but density might
        # still be low (a 1500-char rambling narrative). Use density as
        # a secondary gate only when it's egregiously bad.
        density = info_density_score(text)
        if density["score"] < 8:  # extreme low end
            return True, "low_density"
        return False, ""
    # Below legacy char count: density must rescue it.
    density = info_density_score(text)
    if density["score"] < DENSITY_SCORE_THRESHOLD:
        return True, "low_chars"
    return False, ""


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
    "TAG_OK",
    "TAG_ERR",
    "TAG_WARN",
    "TAG_INFO",
    "TAG_FIRE",
    "TAG_TIME",
    "TAG_DONE",
    "TAG_HEARTBEAT",
    "TAG_TOOL",
    "TAG_STATS",
    "TAG_RESCUE",
    "TAG_ENGINE_AUDIT",
    "TAG_SMART_FILTER",
    "TAG_MMX_OK",
    "TAG_MMX_ERR",
    "TAG_MMX_QUOTA",
    "TAG_TAVILY_OK",
    "TAG_TAVILY_ERR",
    "TAG_TAVILY_FALLBACK",
    "TAG_RELEVANCE",
    "TAG_CFY",
    # Timeouts
    "DEFAULT_TASK_TIMEOUT_S",
    "DEFAULT_GLOBAL_TIMEOUT_S",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_HEARTBEAT_INTERVAL_S",
    # Domains & filters
    "SPAM_DOMAINS",
    "CF_BLACK_DOMAINS",
    "LOWQ_DOMAINS",
    "MEDIUM_ALTERNATIVES",
    "TRACKING_PARAMS",
    "_BLACKLIST_PATH_PATTERNS",
    "_LOWQ_HOST_PATTERNS",
    "BLOCK_KEYWORDS",
    "QUOTA_KEYWORDS",
    "USER_AGENTS",
    "VIEWPORTS",
    # Relevance
    "QUERY_RELEVANCE_MIN_HITRATE",
    "QUERY_CORE_ENTITY_MIN_HITRATE",
    "MIN_CHARS_DEFAULT",
    "VARIANT_KEYS",
    "CN_STOPWORDS",
    "EN_STOPWORDS",
    "PREDICTIVE_KEYWORDS",
    "BULL_KEYWORDS",
    "BEAR_KEYWORDS",
    "public",
]
