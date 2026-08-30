"""Detect local-language expansion targets for a query.

If the user searches for a topic that's strongly tied to a specific
country or region (e.g. ``Japan``, ``南美``, ``法国大革命``), we want
to add a search task in the local language. This module returns the
list of additional language targets to append to the search matrix.

The legacy project kept this list hardcoded in ``deep_search.py``. We
extracted it for two reasons:

1. **Testability** — the detection logic is a pure function on a string.
2. **i18n** — users can add new rules by writing a small plugin module
   (no need to fork the package).

The default ruleset is intentionally narrow: only well-known country /
region keywords. Adding more would risk false positives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class LocalLang:
    """One local-language expansion target."""

    code: str           # ISO 639-1 (best effort)
    name: str           # human-readable Chinese label


#: Default ruleset. Override by passing ``rules=`` to :func:`detect_local_langs`.
DEFAULT_RULES: Final[tuple[tuple[tuple[str, ...], str, str], ...]] = (
    (("日本", "东京", "京都", "大阪", "japan", "tokyo", "kyoto"), "ja", "日文"),
    (("南美", "拉美", "阿根廷", "巴西", "智利", "秘鲁", "south america", "brazil", "argentina"), "es", "西文"),
    (("巴西", "brazil", "lisbon"), "pt", "葡文"),
    (("韩国", "首尔", "korea", "seoul", "kpop", "k-"), "ko", "韩文"),
    (("法国", "巴黎", "france", "paris", "法语", "french"), "fr", "法文"),
    (("德国", "柏林", "germany", "berlin", "德语", "german"), "de", "德文"),
    (("意大利", "罗马", "italy", "rome", "文艺复兴", "renaissance"), "it", "意文"),
    (("俄罗斯", "莫斯科", "russia", "moscow"), "ru", "俄文"),
    (("中东", "阿拉伯", "迪拜", "middle east", "arab", "dubai"), "ar", "阿文"),
    (("印度", "孟买", "india", "mumbai", "hindi"), "hi", "印地文"),
)


def detect_local_langs(
    query: str,
    *,
    rules: tuple[tuple[tuple[str, ...], str, str], ...] = DEFAULT_RULES,
) -> list[LocalLang]:
    """Return the local-language targets relevant to ``query``.

    Args:
        query: free-form search query.
        rules: override the default ruleset (advanced).

    Returns:
        List of :class:`LocalLang` (deduplicated by language code).
        Empty list if no rule matches.

    Examples:
        >>> detect_local_langs("南美历史")[:1]
        [LocalLang(code='es', name='西文')]
        >>> detect_local_langs("Python asyncio")
        []
    """
    if not query:
        return []
    q_lower = query.lower()

    matched: list[LocalLang] = []
    seen: set[str] = set()
    for needles, code, name in rules:
        if code in seen:
            continue
        for n in needles:
            if n.lower() in q_lower:
                matched.append(LocalLang(code=code, name=name))
                seen.add(code)
                break
    return matched


def local_lang_for(code: str) -> LocalLang | None:
    """Resolve a single language code back to its :class:`LocalLang`.

    Useful when the user passes ``--lang=ja`` directly and you want to
    know the human-readable label.

    Returns:
        The matching :class:`LocalLang`, or ``None``.
    """
    for _needles, c, name in DEFAULT_RULES:
        if c == code:
            return LocalLang(code=c, name=name)
    return None


__all__ = ["LocalLang", "DEFAULT_RULES", "detect_local_langs", "local_lang_for"]
