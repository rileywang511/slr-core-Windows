from __future__ import annotations

import json
from pathlib import Path

from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.exports import EXPORTERS
from slr.record import Author, Record
from tests import ReviewFiles


def _setup_review(tmp_path: Path) -> ReviewFiles:
    store = ReviewFiles(tmp_path)
    store.init()
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 2)
    recs = [
        Record(source="openalex", source_id="W1", doi="10.1/a", title="Paper A",
               authors=[Author(family="Smith", given="J")], year=2024,
               venue="Nature", type="article", abstract="Abstract A", url="https://example.org/a"),
        Record(source="openalex", source_id="W2", doi="10.1/b", title="Paper B",
               authors=[Author(family="Jones", given="K")], year=2025,
               venue="Science", type="article", abstract="Abstract B"),
    ]
    store.insert_records(run_id, recs)
    rows = store.all_records()
    plans = deduplicate_occurrences(rows)
    store.write_final_records(plans_to_payload(plans))
    # Mark one as included
    ta_record_id = sorted(record["record_id"] for record in store.final_records())[0]
    store.write_screening_decisions("title_abstract", [
        {"record_id": ta_record_id, "decision": "include", "reason_code": "I1",
         "reason_text": "good", "screener": "rules-v1"},
    ])
    return store


def test_jsonl_export_all(tmp_path: Path):
    store = _setup_review(tmp_path)
    out = tmp_path / "all.jsonl"
    n = EXPORTERS["jsonl"](store.review_dir, out, stage="all")
    assert n == 2
    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert {r["title"] for r in lines} == {"Paper A", "Paper B"}


def test_jsonl_export_included(tmp_path: Path):
    store = _setup_review(tmp_path)
    out = tmp_path / "included.jsonl"
    n = EXPORTERS["jsonl"](store.review_dir, out, stage="included")
    assert n == 1


def test_ris_export_writes_entries(tmp_path: Path):
    store = _setup_review(tmp_path)
    out = tmp_path / "out.ris"
    n = EXPORTERS["ris"](store.review_dir, out, stage="all")
    assert n == 2
    text = out.read_text()
    assert "TY  - JOUR" in text
    assert "DO  - 10.1/a" in text or "DO  - 10.1/b" in text
    assert "UR  - https://example.org/a" in text


def test_csv_export_columns(tmp_path: Path):
    store = _setup_review(tmp_path)
    out = tmp_path / "out.csv"
    n = EXPORTERS["csv"](store.review_dir, out, stage="all")
    assert n == 2
    header = out.read_text().splitlines()[0]
    assert "doi" in header and "title" in header
