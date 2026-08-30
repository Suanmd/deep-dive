"""Reporting layer.

Two pieces:

* :mod:`deep_dive.reporting.builder` — builds the structured
  ``report.md`` (4 sections + low-quality pages).
* :mod:`deep_dive.reporting.capy_summary` — appends the
  Capy-style thematic summary section.
"""

from __future__ import annotations

from .builder import build_report
from .capy_summary import append_capy_section, detect_lang_from_url

__all__ = ["build_report", "append_capy_section", "detect_lang_from_url"]
