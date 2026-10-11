from __future__ import annotations

import json
from pathlib import Path

from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.prisma import compute_prisma, write_outputs
from slr.record import Author, Record
from tests import ReviewFiles


def _make_review(tmp_path: Path) -> ReviewFiles:
    store = ReviewFiles(tmp_path)
    store.init()
    return store


def test_review_files_round_trip(tmp_path: Path):
    store = _make_review(tmp_path)
    run_id = store.insert_source_run(
        source="openalex",
        query_raw="TS=foo",
        query_translated="filter=default.search:foo",
        warnings=[],
        tool_version="0.0.1",
        n_returned=2,
    )
    records = [
        Record(source="openalex", source_id="W1", doi="10.1/a", title="Title A",
               authors=[Author(family="Smith", given="J")], year=2024),
        Record(source="openalex", source_id="W2", doi="10.1/b", title="Title B",
               authors=[Author(family="Jones", given="K")], year=2025),
    ]
    inserted = store.insert_records(run_id, records)
    assert inserted == 2
    assert store.record_count() == 2
    rows = store.all_records()
    assert {r["doi"] for r in rows} == {"10.1/a", "10.1/b"}


def test_duplicate_source_occurrences_are_skipped(tmp_path: Path):
    store = _make_review(tmp_path)
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 1)
    rec = Record(source="openalex", source_id="W1", title="t")
    store.insert_records(run_id, [rec])
    store.insert_records(run_id, [rec])  # second insert is a no-op
    assert store.record_count() == 1


def test_prisma_counts_end_to_end(tmp_path: Path):
    store = _make_review(tmp_path)
    run_openalex = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 3)
    run_epmc = store.insert_source_run("europepmc", "q", "tq", [], "0.0.1", 2)
    # 3 records, two share a DOI (one duplicate across sources)
    store.insert_records(run_openalex, [
        Record(source="openalex", source_id="W1", doi="10.1/x", title="paper one",
               authors=[Author(family="Smith")], year=2024),
        Record(source="openalex", source_id="W2", doi="10.1/y", title="paper two",
               authors=[Author(family="Jones")], year=2024),
        Record(source="openalex", source_id="W3", doi=None, title="paper three",
               authors=[Author(family="Brown")], year=2024),
    ])
    store.insert_records(run_epmc, [
        Record(source="europepmc", source_id="PMID1", doi="10.1/x", title="paper one",
               authors=[Author(family="Smith")], year=2024),
        Record(source="europepmc", source_id="PMID2", doi="10.1/z", title="paper four",
               authors=[Author(family="Lee")], year=2024),
    ])

    rows = store.all_records()
    plans = deduplicate_occurrences(rows)
    store.write_final_records(plans_to_payload(plans))

    data = compute_prisma(tmp_path)
    assert data["identification"]["per_source"] == {"openalex": 3, "europepmc": 2}
    assert data["identification"]["n_total"] == 5
    assert data["deduplication"]["n_after_dedup"] == 4   # one DOI-duplicate merged
    assert data["deduplication"]["n_duplicates_removed"] == 1

    outputs = write_outputs(tmp_path)
    assert Path(outputs["json"]).exists()
    assert Path(outputs["mmd"]).exists()
    payload = json.loads(Path(outputs["json"]).read_text())
    assert payload["deduplication"]["n_after_dedup"] == 4
