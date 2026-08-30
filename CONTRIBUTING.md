# Contributing to deep-dive

Thank you for your interest in contributing! This document explains how to set
up a dev environment, run tests, and submit a clean pull request.

---

## 📋 Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you agree to uphold it.

## 🧰 Development Setup

### Prerequisites

- Python 3.10+
- Git
- (Optional) Tavily API key — for running engine integration tests
- (Optional) The `mmx` CLI binary on `PATH` — for MMX engine tests

### First-time setup

```bash
# Clone your fork
git clone https://github.com/Suanmd/deep-dive.git
cd deep-dive

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # POSIX
# or:  .venv\Scripts\activate     # Windows

# Install runtime + dev dependencies
pip install -r requirements-dev.txt
playwright install chromium

# Install in editable mode (so `python -m deep_dive` works)
pip install -e .

# Sanity check
pytest --collect-only
ruff check src tests
```

## 🧪 Testing

We use **pytest**. Tests live in `tests/` and mirror the source layout.

```bash
# All tests
pytest

# Unit tests only (no network)
pytest tests/unit

# Integration tests
pytest tests/integration

# With coverage
pytest --cov=deep_dive --cov-report=term-missing

# Single file
pytest tests/unit/test_canonical.py -v

# Skip slow / network tests
pytest -m "not slow and not network"
```

When adding tests:

- Unit tests must not hit the network (mock engines / use fixtures).
- Integration tests that hit the network should be marked `@pytest.mark.network`.
- For new modules, add at least one test file under `tests/unit/`.
- Aim for > 80% line coverage on new code.

## 🎨 Style Guide

- **[ruff](https://github.com/astral-sh/ruff)** — lint + import sort
- **[mypy](https://mypy.readthedocs.io/)** — static type checking
- **Type hints** are required for all public functions and methods
- **Docstrings** — Google style for modules and classes; one-line for short helpers

Run before committing:

```bash
ruff check src tests
ruff format --check src tests
mypy src/deep_dive
```

## ⚙️ Configuration Discipline

deep-dive follows a strict rule: **user-tunable values live in `config/` files,
not in Python code**.

When you find yourself adding a hardcoded list, threshold, or path inside
`src/deep_dive/`, stop and put it in `config/defaults.yaml` instead. Read it
back at runtime via `Config` properties.

## 📁 Module Layout

| Adding a... | Goes into |
|-------------|-----------|
| New search engine (Brave, Serper, …) | `src/deep_dive/crawler/engines/` |
| New fetcher (httpx, requests, …) | `src/deep_dive/crawler/fetchers/` |
| New URL filter / canonicalisation step | `src/deep_dive/filters/` |
| New report section | `src/deep_dive/reporting/` |
| New CLI flag | Edit `src/deep_dive/cli.py` (keep argparse minimal) |
| New config key | Edit `config/defaults.yaml` AND `src/deep_dive/config.py` |
| New documentation page | `docs/<topic>.md` + link from `docs/README.md` |

Keep modules small (< 400 LOC). If a module grows, split it.

## 🔀 Pull Request Process

1. **Fork** the repo and create a feature branch:
   ```bash
   git checkout -b feat/my-new-feature
   ```

2. **Make your changes** following the style guide above.

3. **Add tests** for any new functionality.

4. **Update docs** if you change CLI flags, config keys, or public API.

5. **Update `CHANGELOG.md`** under an "Unreleased" section:
   ```markdown
   ## [Unreleased]
   ### Added
   - New Brave search engine behind `--search-engine=brave`
   ```

6. **Run the full check suite**:
   ```bash
   pytest
   ruff check src tests
   ruff format --check src tests
   mypy src/deep_dive
   ```

7. **Commit** with a descriptive message:
   ```
   feat(engines): add Brave search engine

   Implements a new search backend behind `--search-engine=brave`.
   Falls back gracefully when BRAVE_API_KEY is unset.

   Tests: tests/unit/test_brave_engine.py
   Docs: docs/engines.md
   ```

8. **Push and open a PR** — fill out the PR template.

9. **Address review feedback** — squash commits before merge if asked.

## 🐛 Reporting Bugs

Open an issue with:

- `deep-dive --version` (or `python -m deep_dive --version`)
- Python version and OS
- The exact command (with `--debug` flag if possible)
- The output of `<output_dir>/<topic>__<run-id>/summary.json` (sanitize URLs!)
- Expected vs actual behaviour

## 💡 Feature Requests

Open an issue with:

- Use case (what are you trying to do?)
- Proposed API / CLI surface
- Are you willing to implement it?

## 📜 License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
