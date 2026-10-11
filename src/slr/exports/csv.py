from __future__ import annotations

import csv
import json
from pathlib import Path

from slr.exports.jsonl import _select_records


COLUMNS = [
    "record_id", "dedup_method", "doi", "title", "year", "venue",
    "authors", "abstract", "url", "pmid", "pmcid",
    "decision", "reason_code", "reason_text",
]


def export_csv(review_dir: Path, out_path: Path, stage: str | None = None) -> int:
    rows = _select_records(review_dir, stage)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            try:
                authors = json.loads(row.get("authors_json") or "[]")
            except json.JSONDecodeError:
                authors = []
            row["authors"] = "; ".join(
                f"{a.get('family','')}, {a.get('given','')}".strip(", ") for a in authors
            )
            row.pop("raw_json", None)
            w.writerow({k: row.get(k, "") for k in COLUMNS})
    return len(rows)
