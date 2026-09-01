"""Tests for safe_print and Logger."""

from __future__ import annotations

import io

from deep_dive.constants import TAG_ERR, TAG_FIRE, TAG_OK, TAG_WARN
from deep_dive.logging_setup import Logger, safe_print


class TestSafePrint:
    def test_basic_string(self, capsys):
        safe_print("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.err

    def test_writes_to_stderr_by_default(self, capsys):
        safe_print("test message")
        out = capsys.readouterr()
        # Default stream is stderr
        assert "test message" in out.err

    def test_writes_to_provided_stream(self):
        buf = io.StringIO()
        safe_print("to custom stream", file=buf)
        assert "to custom stream" in buf.getvalue()

    def test_replaces_fire_emoji(self, capsys):
        safe_print("Something on fire 🔥")
        captured = capsys.readouterr()
        assert TAG_FIRE in captured.err
        assert "🔥" not in captured.err

    def test_replaces_check_emoji(self, capsys):
        safe_print("Done ✅")
        captured = capsys.readouterr()
        assert TAG_OK in captured.err
        assert "✅" not in captured.err

    def test_replaces_warning_emoji(self, capsys):
        safe_print("Warning ⚠️")
        captured = capsys.readouterr()
        assert TAG_WARN in captured.err
        assert "⚠️" not in captured.err

    def test_replaces_error_emoji(self, capsys):
        safe_print("Error ❌")
        captured = capsys.readouterr()
        assert TAG_ERR in captured.err
        assert "❌" not in captured.err

    def test_chinese_passes_through(self, capsys):
        safe_print("中文测试 字符不乱码")
        captured = capsys.readouterr()
        assert "中文测试" in captured.err

    def test_flushes_by_default(self):
        buf = io.StringIO()
        # flush=True is the default; verify by writing and reading immediately
        safe_print("flushed", file=buf)
        assert "flushed" in buf.getvalue()


class TestLogger:
    def test_log_methods_produce_output(self, capsys):
        logger = Logger(prefix="test")
        logger.info("info msg")
        logger.warn("warn msg")
        logger.error("error msg")
        logger.ok("ok msg")
        captured = capsys.readouterr()
        assert "info msg" in captured.err
        assert "warn msg" in captured.err
        assert "error msg" in captured.err
        assert "ok msg" in captured.err

    def test_disabled_logger_silent(self, capsys):
        logger = Logger(prefix="test")
        logger.disable()
        logger.info("should not appear")
        logger.enable()  # re-enable to verify
        logger.info("should appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.err
        assert "should appear" in captured.err

    def test_prefix_in_output(self, capsys):
        logger = Logger(prefix="myapp")
        logger.info("hello")
        captured = capsys.readouterr()
        assert "[myapp]" in captured.err
