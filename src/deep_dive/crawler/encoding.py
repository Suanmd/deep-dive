"""Smart HTML byte decoding with multiple fallback strategies.

The legacy fetcher used ``requests.Response.text`` which falls back to
``ISO-8859-1`` per HTTP/1.1 §3.7.1 when the ``Content-Type`` header
doesn't declare a charset. On Chinese sites (notably news platforms like
南方+ / nfnews.com) the server sends UTF-8 bytes without declaring charset,
and the request library's ISO-8859-1 fallback double-mojibakes them:

    UTF-8 bytes → decode as Latin-1 → re-encode as UTF-8 (when writing
    to disk) → "数" becomes b'\\xc3\\xa6\\xc2\\x95\\xc2\\xb0' instead of
    b'\\xe6\\x95\\xb0'.

This module provides :func:`smart_decode_bytes` which uses an explicit
resolution chain instead of the unsafe ISO-8859-1 fallback:

    1. Explicit ``hint`` (e.g. Content-Type charset or
       ``requests.Response.apparent_encoding``).
    2. ``charset_normalizer.from_bytes`` best-match detection.
    3. ``<meta charset="...">`` extraction from the HTML head.
    4. UTF-8 with ``errors="replace"`` as last resort.
"""

from __future__ import annotations

import re

try:
    from charset_normalizer import from_bytes as _cn_from_bytes

    _HAS_CHARSET_NORMALIZER = True
except ImportError:  # pragma: no cover
    _HAS_CHARSET_NORMALIZER = False


# Charset detection candidate set.
#
# Without restrictions, ``charset_normalizer`` has a known preference bug
# for short CJK byte sequences: ``b'\\xca\\xfd'`` (GBK "数") is misdetected
# as Big5 ("杅") because Big5's first/second-byte ranges have higher
# validity density than GBK for ambiguous 2-byte samples. Restricting the
# candidate set to common encodings makes GB18030/GBK win for Mainland
# Chinese content while still covering other major scripts.
_CANDIDATE_ENCODINGS: tuple[str, ...] = (
    "utf-8",
    "gb18030",  # Mainland China (superset of GBK, also covers all Unicode)
    "gbk",
    "gb2312",
    "big5",  # Taiwan / Hong Kong
    "euc-kr",  # Korean
    "shift_jis",  # Japanese
    "euc-jp",  # Japanese (rare)
    "iso-8859-1",
    "windows-1252",
)


# Pre-compiled: scans the first ~8 KB of HTML for <meta charset="...">.
# Matches both HTML4 (<meta http-equiv="Content-Type" content="...charset=...">)
# and HTML5 (<meta charset="...">) forms via the charset= token.
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z][A-Za-z0-9_\-:]*)""",
    re.IGNORECASE,
)


def _try_decode(content: bytes, encoding: str) -> str | None:
    """Attempt strict decode; return None on failure."""
    if not encoding:
        return None
    try:
        return content.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None


def smart_decode_bytes(
    content: bytes,
    *,
    hint: str | None = None,
    html_head_size: int = 8192,
) -> str:
    """Decode HTML bytes to ``str`` using a multi-strategy fallback chain.

    Args:
        content: raw response bytes. Empty bytes return empty string.
        hint: optional encoding hint (e.g. ``resp.apparent_encoding``,
            Content-Type charset, or explicit ``TAVILY_ENCODING``).
            Strict-decoded first; on failure we fall through.
        html_head_size: how many leading bytes to scan for ``<meta
            charset=...>``. 8 KB is plenty (charset declarations appear
            in the first 1-2 KB on every compliant page).

    Returns:
        Decoded string. Never raises; always returns *something*
        usable (worst case is UTF-8 with ``errors="replace"``).

    Notes:
        * Never returns ``resp.text``-style ISO-8859-1 fallback — that's
          the bug this module exists to avoid.
        * Resolution order: hint → UTF-8 → GB18030 → charset_normalizer
          → meta-charset → permissive UTF-8.

    Examples:
        >>> smart_decode_bytes("数".encode("utf-8"))
        '数'
        >>> smart_decode_bytes("数".encode("gbk"))
        '数'
        >>> smart_decode_bytes(b"")
        ''
    """
    if not content:
        return ""

    # 1. Explicit hint (e.g. Content-Type charset).
    decoded = _try_decode(content, hint) if hint else None
    if decoded is not None:
        return decoded

    # 2. UTF-8 first — cheap, exact for any valid UTF-8 (incl. ASCII).
    decoded = _try_decode(content, "utf-8")
    if decoded is not None:
        return decoded

    # 3. GB18030 strict. Covers GBK + GB2312 + all CJK Unified Ideographs.
    #    This handles the nfnews.com case (UTF-8 bytes misdeclared as
    #    ISO-8859-1 → strict UTF-8 fails here, but GB18030 recovers).
    #    We put this BEFORE charset_normalizer because for short CJK
    #    byte samples (e.g. ``b'\\xca\\xfd'``) charset_normalizer has a
    #    known preference bug: it picks Big5 ("杅") over GBK ("数")
    #    because Big5's valid-pair coverage is denser. Restricting
    #    candidates to UTF-8 + GB-family doesn't help because the
    #    detector still ranks Big5 above GBK.
    decoded = _try_decode(content, "gb18030")
    if decoded is not None:
        return decoded

    # 4. charset_normalizer best-match (fallback for non-UTF-8/GB scripts).
    if _HAS_CHARSET_NORMALIZER:
        try:
            detected = _cn_from_bytes(
                content[:65536],
            ).best()
            if detected and detected.encoding:
                decoded = _try_decode(content, detected.encoding)
                if decoded is not None:
                    return decoded
        except Exception:
            pass

    # 5. Meta charset in HTML head.
    try:
        m = _META_CHARSET_RE.search(content[:html_head_size])
        if m:
            enc = m.group(1).decode("ascii", errors="ignore").strip().lower()
            decoded = _try_decode(content, enc)
            if decoded is not None:
                return decoded
    except Exception:
        pass

    # 6. UTF-8 fallback (permissive).
    return content.decode("utf-8", errors="replace")


def is_likely_double_mojibake(s: str, *, sample_size: int = 200) -> bool:
    """Heuristic: does ``s`` look like UTF-8-decoded Latin-1 garbage?

    Useful as a defense-in-depth signal in callers that already decoded
    bytes: if the result string has the typical "â²" / "æ°" pattern of
    double-mojibake (Latin-1 chars re-encoded as UTF-8), the original
    bytes were probably misdecoded upstream and the caller should
    consider retrying with explicit encoding.

    Args:
        s: decoded string to inspect.
        sample_size: how many leading chars to check.

    Returns:
        True if the sample contains a high density of Latin-1-range
        Unicode points (U+0080..U+00FF) that *aren't* standard ASCII
        punctuation — the signature of mojibake.

    Examples:
        >>> is_likely_double_mojibake("数学费马")
        False
        >>> is_likely_double_mojibake("æ\u0095°å\xad¦æ°\u00b9ç»\x9fç\x90\x86")
        True
    """
    if not s:
        return False
    sample = s[:sample_size]
    if not sample:
        return False
    suspect = 0
    for ch in sample:
        cp = ord(ch)
        # Latin-1 supplement range: U+0080..U+00FF, excluding common
        # ASCII-compatible punctuation.
        if 0x80 <= cp <= 0xFF and ch not in " ¡¢£¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿":
            suspect += 1
    # Threshold: >15% of sample chars in Latin-1 supplement (mojibake text
    # typically has 50%+; genuine French/Polish etc. usually <5%).
    return suspect / len(sample) > 0.15


__all__ = ["smart_decode_bytes", "is_likely_double_mojibake"]
