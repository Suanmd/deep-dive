"""Allow ``python -m deep_dive`` to invoke the CLI."""

from __future__ import annotations

from deep_dive.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
