from __future__ import annotations

import json
from pathlib import Path

import pytest

from slr import frames
from slr.record import Record


def test_source_evidence_is_one_append_only_ledger(tmp_path: Path):
    frames.init(tmp_path)
    run_id = frames.append_source_run(
        tmp_path, "openalex", "query", "translated", ["query degraded"], "0.1.0", 1,
    )
    frames.append_provenance(tmp_path, run_id, [
        Record(source="openalex", source_id="W1", title="First", raw={"id": "W1"}),
    ])
    lines = [json.loads(line) for line in (tmp_path / frames.EVIDENCE).read_text().splitlines()]

    assert [line["type"] for line in lines] == ["source_run", "warning", "occurrence"]
    assert lines[-1]["raw"] == {"id": "W1"}
    run = frames.source_run_summary(tmp_path)[0]
    assert run["source"] == "openalex"
    assert run["n_returned"] == 1
    assert not (tmp_path / "raw").exists()


def test_legacy_decision_columns_are_read_and_rewritten_with_clear_names(tmp_path: Path):
    """Existing review directories upgrade decision columns on their next append."""
    frames.init(tmp_path)
    screen_path = tmp_path / frames.screen_file("title_abstract")
    screen_path.write_text("id,cluster_id,decision,screener\n1,9,include,rules-v1\n")

    legacy_rows = frames.read_decisions(tmp_path, "title_abstract")
    assert legacy_rows[0]["record_id"] == 9

    frames.append_decision(tmp_path, "title_abstract", {
        "record_id": 10,
        "decision": "exclude",
        "screener": "rules-v1",
    })
    assert screen_path.read_text().splitlines()[0].split(",")[1] == "record_id"
    assert [row["record_id"] for row in frames.read_decisions(tmp_path, "title_abstract")] == [9, 10]


def test_legacy_source_files_migrate_to_the_evidence_ledger(tmp_path: Path):
    frames.init(tmp_path)
    (tmp_path / "source_runs.csv").write_text(
        "id,source,query_raw,query_translated,translation_warnings_json,ran_at,tool_version,"
        "n_returned,raw_response_path,raw_response_sha256,run_kind\n"
        "1,openalex,q,t,[],2026-01-01T00:00:00Z,0.1,1,raw/openalex.jsonl,abc,search\n"
    )
    (tmp_path / "provenance.csv").write_text(
        "id,record_id,source_run_id,source,source_id,doi,title,abstract,authors_json,year,"
        "venue,type,url,pmid,pmcid,match_method,similarity,selected_occurrence_id,raw_json,ingested_at\n"
        '1,,1,openalex,W1,,A title,,[],2024,,,,,,,,,"{""id"": ""W1""}",2026-01-01T00:00:00Z\n'
    )

    rows = frames.active_provenance(tmp_path)

    assert rows[0]["source_id"] == "W1"
    assert rows[0]["raw_json"] == '{"id": "W1"}'
    assert (tmp_path / frames.EVIDENCE).exists()
    assert (tmp_path / "source_runs.csv").exists()  # retained for comparison


def test_latest_successful_pdf_is_selected_after_repeated_fetches(tmp_path: Path):
    frames.init(tmp_path)
    for attempted_at, path in (
        ("2026-08-18T12:00:00Z", "pdfs/first--r1.pdf"),
        ("2026-08-18T12:01:00Z", "pdfs/latest--r1.pdf"),
    ):
        frames.append_fulltext_attempt(tmp_path, {
            "record_id": 1,
            "source": "unpaywall",
            "pdf_sha256": "sha",
            "pdf_path": path,
            "attempted_at": attempted_at,
        })

    assert frames.successful_pdf_per_record(tmp_path) == {1: "pdfs/latest--r1.pdf"}


def test_active_search_snapshot_excludes_superseded_runs_but_keeps_history(tmp_path: Path):
    frames.init(tmp_path)
    first_run = frames.append_source_run(
        tmp_path, "openalex", "first query", "first", [], "0.1", 1
    )
    frames.append_provenance(tmp_path, first_run, [
        Record(source="openalex", source_id="W1", title="Earlier result"),
    ])
    frames.activate_search_runs(tmp_path, [first_run])

    second_run = frames.append_source_run(
        tmp_path, "openalex", "second query", "second", [], "0.1", 1
    )
    frames.append_provenance(tmp_path, second_run, [
        Record(source="openalex", source_id="W2", title="Current result"),
    ])
    frames.activate_search_runs(tmp_path, [second_run])

    assert len(frames.read_provenance(tmp_path)) == 2  # immutable audit history
    assert [row["source_id"] for row in frames.active_provenance(tmp_path)] == ["W2"]
    assert frames.source_run_summary(tmp_path)[0]["n_returned"] == 1


def test_review_write_lock_rejects_a_second_writer(tmp_path: Path):
    with frames.review_write_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already writing"):
            with frames.review_write_lock(tmp_path):
                pass
