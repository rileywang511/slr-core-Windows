"""Scopus CSV importer.

Scopus' "Export → CSV" produces columns including:
  Authors, Title, Year, Source title, Volume, Issue, Page start, Page end,
  Cited by, DOI, Link, Affiliations, Abstract, Author Keywords, Index Keywords,
  Funding Details, References, Document Type, Source, EID
"""

from __future__ import annotations

from pathlib import Path

from slr.importers.base import (
    ImportResult,
    ImportWarning,
    Importer,
    open_csv,
    split_authors_semicolon,
    synth_source_id,
)
from slr.record import Author, Record


_REQUIRED = {"Authors", "Title", "Year"}


class ScopusCsvImporter(Importer):
    name = "scopus"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        cols = set(header)
        return _REQUIRED.issubset(cols) and ("EID" in cols or "Source title" in cols)

    def parse(self, path: Path) -> ImportResult:
        header, rows = open_csv(path)
        out: list[Record] = []
        warnings: list[ImportWarning] = []
        label = path.name
        for i, row in enumerate(rows):
            try:
                rec = self._row_to_record(row, label, i)
                out.append(rec)
            except Exception as exc:
                warnings.append(ImportWarning(i, str(exc), row))
        return ImportResult(records=out, n_rows_read=len(rows),
                            n_records_parsed=len(out), warnings=warnings)

    def _row_to_record(self, row: dict[str, str], label: str, idx: int) -> Record:
        authors = [Author(family=f, given=g) for f, g in split_authors_semicolon(row.get("Authors", ""))]
        year_raw = (row.get("Year") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        doi = (row.get("DOI") or "").strip() or None
        eid = (row.get("EID") or "").strip() or None
        rec = Record(
            source="scopus",
            source_id="",  # filled below
            doi=doi,
            title=(row.get("Title") or "").strip(),
            abstract=(row.get("Abstract") or "").strip() or None,
            authors=authors,
            year=year,
            venue=(row.get("Source title") or "").strip() or None,
            type=(row.get("Document Type") or "").strip() or None,
            url=(row.get("Link") or "").strip() or None,
            raw={**row, "_imported_from": label},
        )
        rec.source_id = eid or synth_source_id(label, idx, rec)
        return rec
