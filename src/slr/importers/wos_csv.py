"""Web of Science CSV / tab-delimited importer.

WoS exports use field-tag columns:
  AU (Authors), AF (Authors Full Name), TI (Title), SO (Source/Journal),
  AB (Abstract), DI (DOI), PY (Year), DT (Document Type), DE (Author Keywords),
  ID (Keywords Plus), UT (WoS Accession Number, e.g. WOS:000XXXXXX), C1 (Affiliations),
  PM (PMID), VL (Volume), IS (Issue), BP (Begin Page), EP (End Page), LA (Language)
"""

from __future__ import annotations

from pathlib import Path

from slr.importers.base import (
    ImportResult,
    ImportWarning,
    Importer,
    open_csv,
    split_authors_wos,
    synth_source_id,
)
from slr.record import Author, Record


_SIGNATURE = {"AU", "TI", "PY"}  # WoS field tags are short uppercase


class WosCsvImporter(Importer):
    name = "wos"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        cols = set(header)
        return _SIGNATURE.issubset(cols) and ("UT" in cols or "DI" in cols or "SO" in cols)

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
        # AF (full names) preferred over AU (abbreviated initials) when both present
        author_field = row.get("AF") or row.get("AU") or ""
        authors = [Author(family=f, given=g) for f, g in split_authors_wos(author_field)]
        year_raw = (row.get("PY") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        doi = (row.get("DI") or "").strip() or None
        ut = (row.get("UT") or "").strip() or None  # WOS accession
        pmid = (row.get("PM") or "").strip() or None
        rec = Record(
            source="wos",
            source_id="",
            doi=doi,
            title=(row.get("TI") or "").strip(),
            abstract=(row.get("AB") or "").strip() or None,
            authors=authors,
            year=year,
            venue=(row.get("SO") or "").strip() or None,
            type=(row.get("DT") or "").strip() or None,
            url=None,
            pmid=pmid,
            raw={**row, "_imported_from": label},
        )
        rec.source_id = ut or pmid or synth_source_id(label, idx, rec)
        return rec
