---
name: Bug Report
about: Report something that is broken or behaving incorrectly
title: "[Bug] "
labels: ["bug"]
---

## Summary

A concise one-line description of the bug.

## Environment

- deep-dive version: `deep-dive --version`
- Python version: `python --version`
- OS: (e.g. macOS 14.4, Ubuntu 22.04, Windows 11)
- mmx CLI installed? (yes/no, version)
- Tavily API key configured? (yes/no)

## Reproduction

The exact command that triggered the bug:

```bash
deep-dive --query "..." --depth normal --output ./repro
```

## Expected behaviour

What you expected to happen.

## Actual behaviour

What actually happened. Include the full error traceback if any.

## Diagnostic artefacts

If possible, attach:

- `<output_dir>/<topic>__<run-id>/summary.json` (sanitise any query-string tokens)
- `<output_dir>/<topic>__<run-id>/raw/<task>/page.html` (first failing URL only)
- Output of running with `--debug` (will write `<topic_dir>/debug/heartbeat.log`)

## Severity

- [ ] Blocks my work (cannot complete a run)
- [ ] Workaround exists (please describe below)
- [ ] Cosmetic / minor

## Additional context

Anything else that might help (search engines tried, network conditions, etc.).
