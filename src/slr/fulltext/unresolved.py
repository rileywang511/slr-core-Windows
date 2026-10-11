"""Write a CSV listing records whose PDF couldn't be acquired by any resolver.

Output columns are designed for manual collection: a human opens the CSV, uses
the DOI URL / link column to grab the PDF, and saves it into the review's
pdfs/ directory.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from slr.fulltext.naming import target_basename
from slr import frames


COLUMNS = [
    "record_id",
    "save_as",          # exact filename to use when saving the manually-downloaded PDF
    "doi",
    "doi_url",
    "title",
    "year",
    "first_author",
    "venue",
    "url",
    "pmid",
    "pmcid",
    "resolvers_tried",
    "last_error",
]


def _first_author_family(authors_json: str | None) -> str:
    if not authors_json:
        return ""
    try:
        authors = json.loads(authors_json)
    except json.JSONDecodeError:
        return ""
    for a in authors:
        if a.get("family"):
            return a["family"]
    return ""


def write_unresolved_csv(review_dir: Path, out_path: Path) -> int:
    """Write rows for every record with NO successful PDF. Returns row count."""
    unresolved = frames.unresolved_record_ids(review_dir)
    records = frames.records_by_id(review_dir, unresolved)
    attempts = frames.attempts_by_record(review_dir)

    rows: list[dict[str, Any]] = []
    for record_id in unresolved:
        rec = records.get(record_id)
        if rec is None:
            continue
        record_attempts = attempts.get(record_id, [])
        resolvers_tried = "|".join(a.get("source") or "" for a in record_attempts)
        last_error = record_attempts[-1].get("error") if record_attempts else None
        rows.append({**rec, "resolvers_tried": resolvers_tried, "last_error": last_error})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            doi = r.get("doi") or ""
            r["doi_url"] = f"https://doi.org/{doi}" if doi else ""
            r["first_author"] = _first_author_family(r.get("authors_json"))
            r["save_as"] = target_basename(
                record_id=r["record_id"],
                doi=r.get("doi"),
                title=r.get("title"),
                year=r.get("year"),
            )
            w.writerow({k: r.get(k, "") for k in COLUMNS})
    return len(rows)
