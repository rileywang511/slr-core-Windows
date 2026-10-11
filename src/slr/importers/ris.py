"""RIS importer.

RIS is the lingua franca exported by Zotero, Mendeley, EndNote, EBSCOhost,
ProQuest, and the "Export → RIS" buttons on most database UIs (including
Scopus, WoS, PubMed, Crossref). Field tag reference:

  TY   Type of reference  (JOUR, BOOK, CHAP, CONF, ...)
  AU   Author             (repeated; "Family, Given")
  TI   Title
  T1   Primary title (alias for TI in some exports)
  AB   Abstract
  N2   Abstract (alternate tag)
  DO   DOI
  PY   Year
  Y1   Date (used by some exporters)
  JO   Journal abbrev
  JF   Journal full
  T2   Secondary title / journal
  SO   Source (some exporters)
  UR   URL
  AN   Accession number (PMID for some sources)
  ID   Reference identifier
"""

from __future__ import annotations

from pathlib import Path

import rispy

from slr.importers.base import ImportResult, ImportWarning, Importer, synth_source_id
from slr.record import Author, Record


class RisImporter(Importer):
    name = "ris"

    @classmethod
    def can_handle(cls, header: list[str], path: Path) -> bool:
        # No CSV header — detection is based on file content, see detect_format
        return path.suffix.lower() in (".ris", ".nbib")

    def parse(self, path: Path) -> ImportResult:
        with path.open(encoding="utf-8-sig") as f:
            try:
                entries = rispy.load(f)
            except Exception as exc:
                raise RuntimeError(f"failed to parse {path.name}: {exc}") from exc
        out: list[Record] = []
        warnings: list[ImportWarning] = []
        label = path.name
        for i, entry in enumerate(entries):
            try:
                out.append(self._entry_to_record(entry, label, i))
            except Exception as exc:
                warnings.append(ImportWarning(i, str(exc), entry, label="entry"))
        return ImportResult(records=out, n_rows_read=len(entries),
                            n_records_parsed=len(out), warnings=warnings)

    def _entry_to_record(self, entry: dict, label: str, idx: int) -> Record:
        # rispy normalises tag names to long-form keys: authors, title, year, etc.
        authors = []
        for a in (entry.get("authors") or entry.get("first_authors") or []):
            if "," in a:
                family, given = a.split(",", 1)
                authors.append(Author(family=family.strip(), given=given.strip() or None))
            else:
                parts = a.rsplit(" ", 1)
                if len(parts) == 2:
                    authors.append(Author(family=parts[0].strip(), given=parts[1].strip() or None))
                else:
                    authors.append(Author(family=a.strip()))
        year_raw = entry.get("year") or (entry.get("date") or "")[:4]
        try:
            year = int(year_raw) if year_raw else None
        except (TypeError, ValueError):
            year = None
        doi = (entry.get("doi") or "").strip() or None
        title = entry.get("title") or entry.get("primary_title") or ""
        venue = (
            entry.get("journal_name")
            or entry.get("secondary_title")
            or entry.get("alternate_title3")
            or ""
        )
        abstract = entry.get("abstract") or entry.get("notes_abstract")
        url = entry.get("url") or entry.get("urls", [None])[0] if entry.get("urls") else entry.get("url")
        pmid = None
        accession = entry.get("accession_number")
        if accession and accession.isdigit():
            pmid = accession
        ref_id = entry.get("id") or entry.get("ref_id")
        rec = Record(
            source="ris",
            source_id="",
            doi=doi,
            title=title.strip(),
            abstract=abstract.strip() if isinstance(abstract, str) else None,
            authors=authors,
            year=year,
            venue=venue.strip() or None,
            type=entry.get("type_of_reference"),
            url=url if isinstance(url, str) else None,
            pmid=pmid,
            raw={**entry, "_imported_from": label},
        )
        rec.source_id = ref_id or doi or synth_source_id(label, idx, rec)
        return rec
