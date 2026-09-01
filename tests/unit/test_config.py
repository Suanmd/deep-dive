"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path

from deep_dive.config import load_config


class TestDefaults:
    def test_default_config(self):
        cfg = load_config()
        assert cfg.depth == "normal"
        assert cfg.lang == "auto"
        assert cfg.search_engine == "auto"
        assert cfg.no_tavily is False
        assert cfg.max_workers == 2
        assert cfg.min_chars == 1500

    def test_config_to_dict_redacts_secrets(self):
        cfg = load_config(overrides={"tavily_api_key": "tvly-secret"})
        d = cfg.to_dict(redact_secrets=True)
        assert d["tavily_api_key"] == "***REDACTED***"

    def test_config_to_dict_shows_secrets_when_requested(self):
        cfg = load_config(overrides={"tavily_api_key": "tvly-secret"})
        d = cfg.to_dict(redact_secrets=False)
        assert d["tavily_api_key"] == "tvly-secret"


class TestEnvOverrides:
    def test_env_output_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEP_DIVE_OUTPUT_DIR", str(tmp_path / "from-env"))
        cfg = load_config()
        assert cfg.output_dir == tmp_path / "from-env"

    def test_env_debug(self, monkeypatch):
        monkeypatch.setenv("DEEP_DIVE_DEBUG", "1")
        cfg = load_config()
        assert cfg.debug is True

    def test_env_tavily_keys(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-K1")
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "tvly-K2")
        cfg = load_config()
        assert cfg.tavily_api_key == "tvly-K1"
        assert cfg.tavily_api_key_backup == "tvly-K2"

    def test_overrides_take_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEP_DIVE_OUTPUT_DIR", str(tmp_path / "env"))
        cfg = load_config(overrides={"output_dir": str(tmp_path / "override")})
        assert cfg.output_dir == tmp_path / "override"


class TestYamlOverrides:
    def test_yaml_overrides_default(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "depth: full\nmax_workers: 5\n",
            encoding="utf-8",
        )
        cfg = load_config(config_file=yaml_file)
        assert cfg.depth == "full"
        assert cfg.max_workers == 5

    def test_yaml_relevance_section(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "relevance:\n  min_hitrate: 0.5\n",
            encoding="utf-8",
        )
        cfg = load_config(config_file=yaml_file)
        assert cfg.relevance_min_hitrate == 0.5

    def test_yaml_depth_config(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "depth_config:\n  normal:\n    topk: 25\n    max_queries: 12\n",
            encoding="utf-8",
        )
        cfg = load_config(config_file=yaml_file)
        assert cfg.depth_config["normal"]["topk"] == 25
        assert cfg.depth_config["normal"]["max_queries"] == 12

    def test_invalid_yaml_falls_back(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(
            "depth: full\nmax_workers: invalid\n",  # max_workers won't apply
            encoding="utf-8",
        )
        cfg = load_config(config_file=yaml_file)
        # depth applied, max_workers invalid → kept default
        assert cfg.depth == "full"
        assert cfg.max_workers == 2

    def test_missing_yaml_returns_defaults(self):
        cfg = load_config(config_file=Path("/nonexistent.yaml"))
        assert cfg.depth == "normal"


class TestDepthHelpers:
    def test_topk_for_default_depth(self):
        cfg = load_config()
        assert cfg.topk_for() == 18  # normal default

    def test_topk_for_quick(self):
        cfg = load_config(overrides={"depth": "quick"})
        assert cfg.topk_for() == 14

    def test_topk_for_full(self):
        cfg = load_config(overrides={"depth": "full"})
        assert cfg.topk_for() == 22

    def test_max_queries_for(self):
        cfg = load_config(overrides={"depth": "full"})
        assert cfg.max_queries_for() == 14


class TestCookieFile:
    def test_returns_none_when_no_candidate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # No cookies.json in cwd
        cfg = load_config(overrides={"output_dir": tmp_path})
        assert cfg.cookie_file is None
