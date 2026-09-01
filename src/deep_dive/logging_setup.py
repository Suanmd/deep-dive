"""UTF-8 safe logging.

Earlier versions had three slightly different
``_safe_print`` implementations across files — each with its own emoji
replacement map, its own ``encode('gbk')`` fallback, and its own
stderr-routing trick. They drifted apart (one supported UTF-8, one
ASCII-stripped to ``?``, etc.), causing inconsistent output across
subprocesses.

This module is the **single source of truth**. Any module that wants to
print status output should either:

    from deep_dive.logging_setup import safe_print

or instantiate :class:`Logger` once at module import.

Design goals
------------

1. **UTF-8 transparent** — never down-convert on the way out.
   PowerShell users can ``chcp 65001`` to see Chinese.
2. **GBK-safe fallback** — if the terminal can't encode a codepoint
   (very old Windows consoles), fall back to ``?`` instead of crashing.
3. **Stderr by default** — stdout is reserved for machine-readable
   output (e.g. ``--print-config`` JSON). Status goes to stderr.
4. **Emoji → ASCII tags** — short ASCII tags replace emojis so logs are
   searchable with plain grep / ripgrep and don't break in terminals
   that mangle multi-byte sequences.
5. **No global monkey-patching** — unlike the legacy
   ``builtins.print = _safe_print`` hack, this module exports
   :func:`safe_print` explicitly. Callers choose to use it.

Why no monkey-patching
----------------------

The legacy approach replaced ``builtins.print`` globally, which made
third-party libraries' logs flow through our emoji substitution
unintentionally and made debugging confusing. Explicit is better than
implicit.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Mapping
from typing import Any, Final, TextIO

from .constants import (
    TAG_DONE,
    TAG_ERR,
    TAG_FIRE,
    TAG_OK,
    TAG_RESCUE,
    TAG_STATS,
    TAG_TIME,
    TAG_TOOL,
    TAG_WARN,
)

# ---------------------------------------------------------------------------
# Emoji replacement table (compile-time constant)
# ---------------------------------------------------------------------------

_EMOJI_REPLACEMENTS: Final[Mapping[str, str]] = {
    "\U0001f525": TAG_FIRE,  # 🔥
    "\u2705": TAG_OK,  # ✅
    "\u26a0\ufe0f": TAG_WARN,  # ⚠️
    "\u274c": TAG_ERR,  # ❌
    "\u23f1\ufe0f": TAG_TIME,  # ⏱️
    "\U0001f380": TAG_RESCUE,  # 🎀
    "\U0001f4ca": TAG_STATS,  # 📊
    "\U0001f50d": "[SCAN]",  # 🔍
    "\U0001f3c6": "[TOP]",  # 🏆
    "\U0001f4a1": "[IDEA]",  # 💡
    "\u26a1": "[FLASH]",  # ⚡
    "\U0001f4cb": "[NOTE]",  # 📋
    "\U0001f6e0\ufe0f": TAG_TOOL,  # 🛠️
    "\U0001f389": TAG_DONE,  # 🎉
}


# ---------------------------------------------------------------------------
# Output stream resolution
# ---------------------------------------------------------------------------


def _resolve_stream(file: TextIO | None) -> TextIO:
    """Return ``file`` if given, else ``sys.stderr`` (status default).

    We default to **stderr** so stdout stays clean for structured
    output (e.g. ``--print-config``).
    """
    return file if file is not None else sys.stderr


# ---------------------------------------------------------------------------
# Core: safe_print
# ---------------------------------------------------------------------------


def safe_print(
    msg: Any,
    *,
    file: TextIO | None = None,
    flush: bool = True,
) -> None:
    """Print ``msg`` in a terminal-safe way.

    Args:
        msg: anything convertible to ``str``.
        file: target stream. Defaults to ``sys.stderr``.
        flush: flush after write. Default ``True`` (PowerShell/agent
            flows need unbuffered output to see progress in real time).
    """
    out = _resolve_stream(file)

    if not isinstance(msg, str):
        msg = str(msg)

    # 1. Emoji → ASCII tags
    for emoji, repl in _EMOJI_REPLACEMENTS.items():
        if emoji in msg:
            msg = msg.replace(emoji, repl)

    # 2. Encode-safety net: if the terminal encoding can't handle some
    #    codepoint, fall back to '?' for those bytes — never crash.
    try:
        encoding = out.encoding or "utf-8"
        msg.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        try:
            msg = msg.encode(encoding, errors="replace").decode(encoding)
        except Exception:
            msg = "[deep-dive] output encoding failed"

    # 3. Write with hard flush. If even ``print`` blows up (extremely
    #    rare closed-pipe scenario), fall back to raw ``write``.
    try:
        print(msg, file=out, flush=flush)
    except Exception:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Logger: lightweight wrapper around safe_print with a stable prefix
# ---------------------------------------------------------------------------


class Logger:
    """Tiny prefix-based logger on top of :func:`safe_print`.

    For richer use cases (file logging, structured output), pass a
    stdlib :class:`logging.Logger` to :class:`StdLibLoggingBridge` instead.
    """

    __slots__ = ("prefix", "_enabled")

    def __init__(self, prefix: str = "deep-dive", *, enabled: bool = True) -> None:
        self.prefix = prefix
        self._enabled = enabled

    def disable(self) -> None:
        """Silence this logger until :meth:`enable` is called."""
        self._enabled = False

    def enable(self) -> None:
        """Re-enable this logger after :meth:`disable`."""
        self._enabled = True

    def _emit(self, level: str, msg: Any) -> None:
        """Internal: route a message through ``safe_print`` if enabled.

        All public ``info``/``warn``/etc. methods funnel through here so
        the enable/disable gate is checked exactly once.
        """
        if not self._enabled:
            return
        safe_print(f"[{self.prefix}] [{level}] {msg}")

    def info(self, msg: Any) -> None:  # noqa: D401
        """Log an INFO-level message (gated by ``enable``/``disable``)."""
        self._emit("INFO", msg)

    def warn(self, msg: Any) -> None:
        """Log a WARN-level message (gated by ``enable``/``disable``)."""
        self._emit("WARN", msg)

    def error(self, msg: Any) -> None:
        """Log an ERR-level message (gated by ``enable``/``disable``)."""
        self._emit("ERR", msg)

    def ok(self, msg: Any) -> None:
        """Log an OK-level message (success marker)."""
        self._emit("OK", msg)

    def debug(self, msg: Any) -> None:
        """Log a DEBUG-level message (always emitted unless disabled)."""
        # Cheap level gate: DEBUG goes through unconditionally because
        # we don't have a global log-level knob here; users that want
        # to silence can call ``Logger.disable()``.
        self._emit("DEBUG", msg)


# ---------------------------------------------------------------------------
# Stdlib bridge (optional)
# ---------------------------------------------------------------------------


class StdLibLoggingBridge:
    """Adapt stdlib :mod:`logging` records to :func:`safe_print`.

    Use when you want file logging + UTF-8-safe console output without
    setting up two parallel logging configurations::

        import logging
        logger = logging.getLogger("deep_dive.foo")
        bridge = StdLibLoggingBridge(logger)
        bridge.install()
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def install(self) -> None:
        """Wire this bridge to the stdlib ``logging`` module.

        Adds a ``StreamHandler`` that wraps each emit with the
        emoji-replacement + UTF-8-safe-print logic.
        """
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler.emit = self._wrap_emit(handler.emit)  # type: ignore[method-assign]
        self._logger.addHandler(handler)

    @staticmethod
    def _wrap_emit(original_emit):
        def emit(record):
            """Closure: replace emojis + hand to original ``emit``."""
            try:
                msg = record.getMessage()
                msg = str(msg)
                for emoji, repl in _EMOJI_REPLACEMENTS.items():
                    if emoji in msg:
                        msg = msg.replace(emoji, repl)
                record.msg = msg
                record.args = ()
            except Exception:
                pass
            return original_emit(record)

        return emit


# ---------------------------------------------------------------------------
# Encoding fix-ups (run-once at import time)
# ---------------------------------------------------------------------------


def _apply_encoding_fixes() -> None:
    """Make stdout/stderr UTF-8 + line-buffered if possible.

    This is a best-effort call. If the stream is already configured by
    a higher-level framework (notebook, web server, etc.), we leave it
    alone. Errors are silently ignored — the goal is graceful
    degradation, not strict correctness.

    On Windows we also probe the **console output code page**
    (independent of Python's stream encoding) and emit a one-time hint
    if it's not UTF-8 (65001). PowerShell and ``cmd.exe`` default to
    the system codepage (typically cp936 on Simplified-Chinese Windows),
    which causes CJK characters written by Python as UTF-8 bytes to
    display as mojibake ("鏋舵瀯" instead of "架构"). Python can't
    change the terminal host's codepage from inside the process; the
    user needs to run ``chcp 65001`` (or use Windows Terminal, which is
    UTF-8 by default). The hint makes that discoverable.

    Note: setting ``PYTHONUNBUFFERED`` here is too late to affect the
    Python startup buffering mode (env vars only affect child
    processes). The ``line_buffering=True`` in ``reconfigure()`` below
    is what actually turns on line-by-line flushing for the already-
    opened streams.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # e.g. pytest captured stream
            continue
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # PYTHONUNBUFFERED only affects new Python processes; harmless to
    # set for any child processes we might spawn.
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    # Windows console codepage hint (see docstring).
    if os.name == "nt":
        try:
            import ctypes

            cp = ctypes.windll.kernel32.GetConsoleOutputCP()  # type: ignore[attr-defined]
            if cp and cp != 65001:
                safe_print(
                    f"[HINT] Windows console code page is {cp}; "
                    f"CJK characters will display as mojibake. "
                    f"Run `chcp 65001` in this terminal, "
                    f"or use Windows Terminal (UTF-8 by default).",
                    file=sys.stderr,
                )
        except Exception:
            pass


_apply_encoding_fixes()


__all__ = [
    "safe_print",
    "Logger",
    "StdLibLoggingBridge",
]
