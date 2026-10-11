"""OpenAlex CSV importer.

Handles the CSV that OpenAlex's web UI produces from "Export → CSV". Column
names vary between OpenAlex export versions; we sniff for the canonical
combination: `id` (containing `openalex.org/W...`) plus `display_name` or
`title`, with familiar fields like `publication_year`, `doi`, `type`, and
either `host_venue` (single column) or `host_venue.display_name`.

Authors are typically rendered as a single string field (`authorships` or
`authors`) joined by ` | ` or `;` — we try both.
"""

from __future__ import annotations

import re
from pathlib import Path

from slr.importers.base import (
    ImportResult,
    ImportWarning,
    Importer,
    _split_one_author,
    open_csv,
    synth_source_id,
)
from slr.record import Author, Record


_TITLE_COLS = ("display_name", "title")
_VENUE_COLS = ("host_venue.display_name", "host_venue", "primary_location.source.display_name")
_AUTHOR_COLS = ("authorships", "authors", "authorships.author.display_name")
_OA_URL_COLS = ("open_access.oa_url", "best_oa_location.url", "primary_location.pdf_url")
_OPENALEX_ID_RE = re.compile(r"openalex\.org/(W\d+)", re.IGNORECASE)


def _first_present(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = row.get(k)
        if v:
            return v.strip()
    return ""


def _split_openalex_authors(s: str) -> list[Author]:
    """OpenAlex CSV uses display_name format (natural order: 'Given Family').

    Multiple authors are separated by ' | ' (older exports) or ';' (newer).
    Within a chunk: last whitespace token = family, everything before = given.
    If the chunk contains a comma we fall back to family-first ('Smith, John').
    """
    if not s:
        return []
    if " | " in s:
        chunks = s.split(" | ")
    else:
        chunks = s.split(";")
    out: list[Author] = []
    for raw in chunks:
        chunk = raw.strip().rstrip(".").strip()
        if not chunk:
            continue
        if "," in chunk:
            family, given = _split_one_author(chunk)
        else:
            parts = chunk.rsplit(" ", 1)
            if len(parts) == 2:
                given, family = parts[0].strip() or None, parts[1].strip()
            else:
                given, family = None, chunk
        if family or given:
            out.append(Author(family=family or None, given=given))
    return out


def _extract_openalex_id(raw_id: str) -> str | None:
    if not raw_id:
        return None
    m = _OPENALEX_ID_RE.search(raw_id)
    if m:
        return m.group(1)
    # bare W-id is fine too
    if raw_id.startswith("W") and raw_id[1:].isdigit():
        return raw_id
    return None


class OpenAlexCsvImporter(Importer):
    name = "openalex"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        cols = set(header)
        if not any(c in cols for c in _TITLE_COLS):
            return False
        # Strong signal: an `id` column (we'll check rows for the URL form too,
        # but column presence is enough at the header-sniff stage) plus an
        # OpenAlex-specific column name.
        if "id" not in cols:
            return False
        openalex_signals = {"openalex", "publication_year", "host_venue",
                            "host_venue.display_name", "authorships",
                            "primary_location.source.display_name",
                            "open_access.oa_url"}
        return bool(openalex_signals & cols)

    def parse(self, path: Path) -> ImportResult:
        header, rows = open_csv(path)
        out: list[Record] = []
        warnings: list[ImportWarning] = []
        label = path.name
        for i, row in enumerate(rows):
            try:
                out.append(self._row_to_record(row, label, i))
            except Exception as exc:
                warnings.append(ImportWarning(i, str(exc), row))
        return ImportResult(records=out, n_rows_read=len(rows),
                            n_records_parsed=len(out), warnings=warnings)

    def _row_to_record(self, row: dict[str, str], label: str, idx: int) -> Record:
        raw_id = (row.get("id") or row.get("openalex") or "").strip()
        oa_id = _extract_openalex_id(raw_id)
        title = _first_present(row, _TITLE_COLS)
        venue = _first_present(row, _VENUE_COLS) or None
        author_str = _first_present(row, _AUTHOR_COLS)
        authors = _split_openalex_authors(author_str)
        year_raw = (row.get("publication_year") or "").strip()
        try:
            year = int(year_raw[:4]) if year_raw else None
        except ValueError:
            year = None
        # DOI sometimes arrives as a URL — Record.normalize_doi handles that.
        doi = (row.get("doi") or "").strip() or None
        url = _first_present(row, _OA_URL_COLS) or None
        rec_type = (row.get("type") or "").strip() or None
        rec = Record(
            source="openalex",
            source_id="",
            doi=doi,
            title=title,
            abstract=(row.get("abstract") or "").strip() or None,
            authors=authors,
            year=year,
            venue=venue,
            type=rec_type,
            url=url,
            raw={**row, "_imported_from": label},
        )
        rec.source_id = oa_id or synth_source_id(label, idx, rec)
        return rec
