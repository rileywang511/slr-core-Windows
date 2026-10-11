"""Predictable, human-readable PDF filenames per final record.

Each record gets a readable title-based filename plus its stable record ID, so:

  - The auto-downloader writes to `pdfs/<year>-<title>--r<id>.pdf`.
  - The unresolved.csv lists the exact same filename.
  - The user downloads the missing PDFs manually and saves them with the
    exact filename listed in the CSV. No ingest command needed — `slr convert`
    scans the pdfs/ directory at the start of its run and picks up any newly
    present files when full-text screening begins.

The record-ID suffix makes names collision-safe. Content hashes remain in the
acquisition and conversion evidence files for integrity checks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SAFE_CHARS_RE = re.compile(r"[^a-z0-9]+")


def target_basename(record_id: int, doi: str | None, title: str | None, year: int | None) -> str:
    """Return a stable, readable target filename for this final record."""
    label = _slug(title or doi or "untitled-paper")[:72].strip("-") or "untitled-paper"
    prefix = str(year) if year else "undated"
    return f"{prefix}-{label}--r{record_id}.pdf"


def target_basename_for_row(row: dict[str, Any]) -> str:
    """Build a target basename for a final record row."""
    return target_basename(
        record_id=row["record_id"],
        doi=row.get("doi"),
        title=row.get("title"),
        year=row.get("year"),
    )


def _slug(value: str) -> str:
    return _SAFE_CHARS_RE.sub("-", value.lower()).strip("-")


# Matches our record-ID suffix so the scan can safely ignore unrelated files.
PDF_FILENAME_RE = re.compile(r"^.+--r\d+\.pdf$", re.IGNORECASE)


def is_target_filename(name: str | Path) -> bool:
    return bool(PDF_FILENAME_RE.match(Path(name).name))
