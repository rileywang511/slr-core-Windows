"""PubMed CSV importer.

PubMed UI "Send To → File → CSV" produces:
  PMID, Title, Authors, Citation, First Author, Journal/Book, Publication Year,
  Create Date, PMCID, NIHMS ID, DOI
"""

from __future__ import annotations

from pathlib import Path

from slr.importers.base import (
    ImportResult,
    ImportWarning,
    Importer,
    open_csv,
    split_authors_pubmed_comma,
    synth_source_id,
)
from slr.record import Author, Record


_REQUIRED = {"PMID", "Title"}


class PubMedCsvImporter(Importer):
    name = "pubmed"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        return _REQUIRED.issubset(set(header))

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
        authors = [Author(family=f, given=g) for f, g in split_authors_pubmed_comma(row.get("Authors", ""))]
        year_raw = (row.get("Publication Year") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        pmid = (row.get("PMID") or "").strip() or None
        pmcid = (row.get("PMCID") or "").strip() or None
        doi = (row.get("DOI") or "").strip() or None
        rec = Record(
            source="pubmed",
            source_id="",
            doi=doi,
            title=(row.get("Title") or "").strip(),
            abstract=None,  # PubMed CSV doesn't include abstracts
            authors=authors,
            year=year,
            venue=(row.get("Journal/Book") or "").strip() or None,
            type=None,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            pmid=pmid,
            pmcid=pmcid,
            raw={**row, "_imported_from": label},
        )
        rec.source_id = pmid or synth_source_id(label, idx, rec)
        return rec
