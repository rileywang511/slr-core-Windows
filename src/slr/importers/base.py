from __future__ import annotations

import csv
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from slr.record import Record


@dataclass
class ImportResult:
    records: list[Record]
    n_rows_read: int
    n_records_parsed: int
    warnings: list["ImportWarning"] = field(default_factory=list)


@dataclass(frozen=True)
class ImportWarning:
    """A failed input row retained verbatim in the review evidence ledger."""

    row_number: int
    message: str
    raw: dict[str, object]
    label: str = "row"
    code: str = "record_parse_failed"

    def __str__(self) -> str:
        return f"{self.label} {self.row_number}: {self.message}"


def synth_source_id(file_label: str, row_index: int, record: Record) -> str:
    """Stable id for imported records lacking native IDs.

    Combines the file label + row index + a hash of (title, first author, year)
    so the SAME row in the SAME file yields the same id on re-import. The
    provenance writer checks that source-ID pair before appending it again.
    """
    first_author = record.first_author_lastname() or ""
    payload = f"{record.title}|{first_author}|{record.year or ''}"
    h = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{file_label}#{row_index}:{h}"


def open_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV file with sniffed dialect and BOM-tolerant decoding.

    Returns (header, rows). Tries utf-8-sig first, falls back to utf-16 (some
    WoS exports), then latin-1 as a last resort. Detects delimiter via csv.Sniffer
    on the first KB; falls back to comma.
    """
    raw = path.read_bytes()
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"could not decode {path} with any common encoding")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel  # default to comma
    # Preserve surplus values under a readable key instead of DictReader's
    # default ``None`` key. They then remain inspectable in evidence.jsonl.
    reader = csv.DictReader(text.splitlines(), dialect=dialect, restkey="<unnamed column>")
    header = reader.fieldnames or []
    rows = list(reader)
    return [h.strip() for h in header], rows


def _split_one_author(chunk: str) -> tuple[str, str | None]:
    """Parse a single author chunk into (family, given).

    Handles: 'Smith, John', 'Smith, J.', 'Smith J', 'Smith John', 'Smith'.
    Comma-containing chunks: split on the first comma (family-first).
    Otherwise: last whitespace token = initials/given, rest = family.
    """
    chunk = chunk.strip().rstrip(".").strip()
    if not chunk:
        return ("", None)
    if "," in chunk:
        family, given = chunk.split(",", 1)
        return (family.strip(), given.strip() or None)
    parts = chunk.rsplit(" ", 1)
    if len(parts) == 2 and len(parts[1]) <= 4 and parts[1].replace(".", "").isalpha():
        # Looks like "Family Initials" (e.g. "Smith J" or "Smith JK")
        return (parts[0].strip(), parts[1].strip() or None)
    return (chunk, None)


def split_authors_semicolon(s: str) -> list[tuple[str, str | None]]:
    """Split semicolon-delimited author lists.

    Handles both 'Smith, J.; Jones, K.' (Scopus) and 'Smith J; Jones K' (WoS AU).
    """
    if not s:
        return []
    return [_split_one_author(c) for c in s.split(";") if c.strip()]


def split_authors_wos(s: str) -> list[tuple[str, str | None]]:
    """Split WoS author column ('AF' uses 'Family, Given', 'AU' uses 'Family Initials').
    Both are semicolon-delimited; _split_one_author handles either form.
    """
    return split_authors_semicolon(s)


def split_authors_pubmed_comma(s: str) -> list[tuple[str, str | None]]:
    """Split 'Smith J, Jones K, Brown L' (PubMed CSV — comma-separated authors).

    Each author is `Family Initials` with no internal comma.
    """
    out: list[tuple[str, str | None]] = []
    if not s:
        return out
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.rsplit(" ", 1)
        if len(parts) == 2:
            family, given = parts
            out.append((family.strip(), given.strip() or None))
        else:
            out.append((chunk, None))
    return out


class Importer(ABC):
    name: str
    # Canonical source label used in the evidence ledger / PRISMA. Defaults to `name`
    # but importers handling multiple shapes of the same database (e.g.
    # OpenAlex CSV vs JSON) override this to a shared label.
    source_label: str | None = None

    @classmethod
    @abstractmethod
    def can_handle(cls, header: list[str], path: Path) -> bool: ...

    @abstractmethod
    def parse(self, path: Path) -> ImportResult: ...

    @classmethod
    def get_source_label(cls) -> str:
        return cls.source_label or cls.name
