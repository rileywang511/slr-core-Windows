from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import rispy

from slr.exports.jsonl import _select_records


_TYPE_MAP = {
    "article": "JOUR",
    "journal-article": "JOUR",
    "book": "BOOK",
    "book-chapter": "CHAP",
    "review": "JOUR",
    "preprint": "JOUR",
    "Journal Article": "JOUR",
}


def _to_ris_entry(row: dict[str, Any]) -> dict[str, Any]:
    authors = []
    try:
        authors_obj = json.loads(row.get("authors_json") or "[]")
    except json.JSONDecodeError:
        authors_obj = []
    for a in authors_obj:
        family = a.get("family") or ""
        given = a.get("given") or ""
        if family or given:
            authors.append(f"{family}, {given}".strip(", "))

    entry: dict[str, Any] = {
        "type_of_reference": _TYPE_MAP.get(row.get("type") or "", "JOUR"),
        "title": row.get("title") or "",
        "authors": authors,
        "abstract": row.get("abstract") or "",
        "doi": row.get("doi") or "",
        "year": str(row.get("year")) if row.get("year") else "",
        "journal_name": row.get("venue") or "",
        # rispy writes RIS UR tags from its plural ``urls`` field; ``url`` is
        # silently ignored and used to lose citation landing pages on export.
        "urls": [row["url"]] if row.get("url") else [],
    }
    return entry


def export_ris(review_dir: Path, out_path: Path, stage: str | None = None) -> int:
    rows = _select_records(review_dir, stage)
    entries = [_to_ris_entry(r) for r in rows]
    with out_path.open("w", encoding="utf-8") as f:
        rispy.dump(entries, f)
    return len(entries)
