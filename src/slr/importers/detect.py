"""Auto-detect the right importer for a given file."""

from __future__ import annotations

from pathlib import Path


def detect_format(path: Path) -> str:
    """Return the registry key of the best-matching importer ('scopus', 'wos',
    'pubmed', 'europepmc', 'ris', or 'csv'). Raises if nothing fits."""
    # Avoid circular import
    from slr.importers import (
        EuropePMCCsvImporter, GenericCsvImporter, OpenAlexCsvImporter,
        PubMedCsvImporter, ScopusCsvImporter, WosCsvImporter,
    )
    from slr.importers.base import open_csv

    ext = path.suffix.lower()
    if ext in (".ris", ".nbib"):
        return "ris"
    if ext in (".bib", ".bibtex"):
        raise NotImplementedError("BibTeX import not yet supported")
    if ext in (".json", ".jsonl", ".ndjson"):
        return "openalex_json"

    # CSV-ish — sniff the header
    try:
        header, _rows = open_csv(path)
    except Exception as exc:
        raise RuntimeError(f"could not parse {path.name} as CSV: {exc}") from exc

    # Order matters: most specific signatures first
    if ScopusCsvImporter.can_handle(header, path):
        return "scopus"
    if WosCsvImporter.can_handle(header, path):
        return "wos"
    if OpenAlexCsvImporter.can_handle(header, path):
        return "openalex"
    if EuropePMCCsvImporter.can_handle(header, path):
        return "europepmc"
    if PubMedCsvImporter.can_handle(header, path):
        return "pubmed"
    if GenericCsvImporter.can_handle(header, path):
        return "csv"
    raise RuntimeError(
        f"could not auto-detect format for {path.name}; columns: {header}. "
        f"Pass --format explicitly."
    )
