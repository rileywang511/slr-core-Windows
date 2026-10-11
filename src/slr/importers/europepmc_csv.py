"""Europe PMC CSV importer.

Europe PMC's "Export results → CSV" produces columns like:
  id, source, pmid, pmcid, doi, title, authorString, journalTitle, pubYear,
  pageInfo, journalIssn, citedByCount, hasReferences, hasTextMinedTerms,
  hasFullText, hasPDF
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


_REQUIRED = {"title"}
_SIGNATURE = {"authorString", "journalTitle", "pubYear", "pmcid"}


class EuropePMCCsvImporter(Importer):
    name = "europepmc"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        cols = set(header)
        return _REQUIRED.issubset(cols) and bool(_SIGNATURE & cols)

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
        # Europe PMC's authorString format: "Smith J, Jones K, Brown L."
        authors_str = (row.get("authorString") or "").rstrip(".")
        authors = [Author(family=f, given=g) for f, g in split_authors_pubmed_comma(authors_str)]
        year_raw = (row.get("pubYear") or "").strip()
        try:
            year = int(year_raw) if year_raw else None
        except ValueError:
            year = None
        pmid = (row.get("pmid") or "").strip() or None
        pmcid = (row.get("pmcid") or "").strip() or None
        doi = (row.get("doi") or "").strip() or None
        native_id = (row.get("id") or "").strip()
        url = None
        if pmcid:
            url = f"https://europepmc.org/article/PMC/{pmcid}"
        elif pmid:
            url = f"https://europepmc.org/article/MED/{pmid}"
        rec = Record(
            source="europepmc",
            source_id="",
            doi=doi,
            title=(row.get("title") or "").strip(),
            abstract=(row.get("abstract") or "").strip() or None,  # not in standard export but tolerate
            authors=authors,
            year=year,
            venue=(row.get("journalTitle") or "").strip() or None,
            type=(row.get("pubType") or row.get("source") or "").strip() or None,
            url=url,
            pmid=pmid,
            pmcid=pmcid,
            raw={**row, "_imported_from": label},
        )
        rec.source_id = native_id or pmid or pmcid or synth_source_id(label, idx, rec)
        return rec
