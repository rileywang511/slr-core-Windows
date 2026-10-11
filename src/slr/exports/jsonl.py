from __future__ import annotations

import json
from pathlib import Path

from slr import frames


def _select_records(review_dir: Path, stage: str | None) -> list[dict]:
    records = frames.records_by_id(review_dir)

    if stage in (None, "all"):
        return [records[record_id] for record_id in sorted(records)]

    # "included" = the final stage that has decisions. Prefer full_text if any
    # full_text decisions exist; otherwise fall back to title_abstract.
    if stage == "included":
        target_stage = "full_text" if frames.n_full_text_decisions(review_dir) > 0 else "title_abstract"
        include_only = True
    elif stage == "passed_ta":
        target_stage, include_only = "title_abstract", True
    elif stage == "passed_ft":
        target_stage, include_only = "full_text", True
    elif stage == "screened_ta":
        target_stage, include_only = "title_abstract", False
    elif stage == "screened_ft":
        target_stage, include_only = "full_text", False
    else:
        target_stage, include_only = stage, False

    out: list[dict] = []
    for d in frames.latest_decisions(review_dir, target_stage):
        if include_only and d.get("decision") != "include":
            continue
        rec = records.get(d["record_id"])
        if rec is None:
            continue
        out.append({
            **rec,
            "decision": d.get("decision"),
            "reason_code": d.get("reason_code"),
            "reason_text": d.get("reason_text"),
        })
    return out


def export_jsonl(review_dir: Path, out_path: Path, stage: str | None = None) -> int:
    rows = _select_records(review_dir, stage)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            # drop raw_json for compactness; users can read records.csv for raw
            row.pop("raw_json", None)
            f.write(json.dumps(row, default=str) + "\n")
    return len(rows)
