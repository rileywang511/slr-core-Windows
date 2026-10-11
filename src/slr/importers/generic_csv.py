"""Generic CSV importer with heuristic column mapping.

Tolerates any CSV by matching column names (case-insensitive, trimmed) against
known aliases. Use this when the source platform isn't natively supported, or
when the user has produced a CSV themselves from Zotero, Excel, etc.

Columns are recognised under any of these aliases:
  title:       Title, TI, Article Title, title
  doi:         DOI, doi, DI
  year:        Year, PY, Publication Year, pubYear, year
  authors:     Authors, AU, AF, Author, authorString, authors
  abstract:    Abstract, AB, abstract
  venue:       Source title, Source, SO, Journal, journalTitle, Journal/Book, venue
  type:        Document Type, DT, pubType, type
  url:         URL, Link, url
  pmid:        PMID, PM, pmid
  pmcid:       PMCID, pmcid
"""

from __future__ import annotations

from pathlib import Path

from slr.importers.base import (
    ImportResult,
    ImportWarning,
    Importer,
    open_csv,
    split_authors_pubmed_comma,
    split_authors_semicolon,
    synth_source_id,
)
from slr.record import Author, Record


_ALIASES: dict[str, list[str]] = {
    "title": ["title", "ti", "article title"],
    "doi": ["doi", "di"],
    "year": ["year", "py", "publication year", "pubyear"],
    "authors": ["authors", "au", "af", "author", "authorstring"],
    "abstract": ["abstract", "ab"],
    "venue": ["source title", "source", "so", "journal", "journaltitle", "journal/book", "venue"],
    "type": ["document type", "dt", "pubtype", "type"],
    "url": ["url", "link"],
    "pmid": ["pmid", "pm"],
    "pmcid": ["pmcid"],
}


def _map_columns(header: list[str]) -> dict[str, str]:
    """Return {canonical_field: actual_column_name} for whatever the header has."""
    norm = {h.strip().lower(): h for h in header if h}
    mapping: dict[str, str] = {}
    for canon, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in norm:
                mapping[canon] = norm[alias]
                break
    return mapping


def _split_authors(raw: str) -> list[tuple[str, str | None]]:
    """Auto-pick a splitter. Semicolons strongly suggest Scopus-style; otherwise PubMed-comma."""
    if not raw:
        return []
    if ";" in raw:
        return split_authors_semicolon(raw)
    return split_authors_pubmed_comma(raw)


class GenericCsvImporter(Importer):
    name = "csv"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        mapping = _map_columns(header)
        return "title" in mapping  # minimum useful threshold

    def parse(self, path: Path) -> ImportResult:
        header, rows = open_csv(path)
        mapping = _map_columns(header)
        if "title" not in mapping:
            raise RuntimeError(f"no usable 'title' column found in {path.name}; columns: {header}")
        out: list[Record] = []
        warnings: list[ImportWarning] = []
        label = path.name
        for i, row in enumerate(rows):
            try:
                out.append(self._row_to_record(row, mapping, label, i))
            except Exception as exc:
                warnings.append(ImportWarning(i, str(exc), row))
        return ImportResult(records=out, n_rows_read=len(rows),
                            n_records_parsed=len(out), warnings=warnings)

    def _row_to_record(self, row: dict[str, str], mapping: dict[str, str],
                       label: str, idx: int) -> Record:
        def get(k: str) -> str:
            col = mapping.get(k)
            return (row.get(col) or "").strip() if col else ""

        year_raw = get("year")
        try:
            year = int(year_raw[:4]) if year_raw else None
        except ValueError:
            year = None
        authors = [Author(family=f, given=g) for f, g in _split_authors(get("authors"))]
        rec = Record(
            source="csv",
            source_id="",
            doi=get("doi") or None,
            title=get("title"),
            abstract=get("abstract") or None,
            authors=authors,
            year=year,
            venue=get("venue") or None,
            type=get("type") or None,
            url=get("url") or None,
            pmid=get("pmid") or None,
            pmcid=get("pmcid") or None,
            raw={**row, "_imported_from": label, "_column_mapping": mapping},
        )
        rec.source_id = synth_source_id(label, idx, rec)
        return rec
