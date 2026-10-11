"""`slr export-included` — CSV of full-text-passed records with paths,
reasoning, and provenance."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from slr.cli import app
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.record import Author, Record
from tests import ReviewFiles

runner = CliRunner()


def _setup(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(
        "review_id: x\ntitle: t\nquery: q\nsources: [openalex]\n"
    )
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 2)
    store.insert_records(run_id, [
        Record(source="openalex", source_id="W1", doi="10.1/a", title="Included paper",
               authors=[Author(family="Smith", given="J")], year=2024, venue="Nature"),
        Record(source="openalex", source_id="W2", doi="10.1/b", title="Excluded paper",
               authors=[Author(family="Doe")], year=2023),
    ])
    store.write_final_records(plans_to_payload(deduplicate_occurrences(store.all_records())))
    by_title = {record["title"]: record["record_id"] for record in store.final_records()}
    inc, exc = by_title["Included paper"], by_title["Excluded paper"]

    # TA: both pass; FT: only `inc` is included.
    for cid in (inc, exc):
        store.append_screening_decision("title_abstract", {
            "record_id": cid, "decision": "include", "reason_code": "I1",
            "reason_text": "ta ok", "screener": "rules-v1"})
    store.append_screening_decision("full_text", {
        "record_id": inc, "decision": "include", "reason_code": "I2",
        "reason_text": "meets all criteria", "screener": "anthropic:claude-sonnet-4-6"})
    store.append_screening_decision("full_text", {
        "record_id": exc, "decision": "exclude", "reason_code": "E3",
        "reason_text": "wrong population", "screener": "anthropic:claude-sonnet-4-6"})

    # PDF + conversion for the included record.
    store.insert_fulltext_attempt({
        "record_id": inc, "source": "unpaywall", "url": "http://x", "http_status": 200,
        "bytes": 99, "pdf_sha256": "deadbeef", "pdf_path": "pdfs/abcd123456.pdf",
        "attempted_at": "2026-05-18", "error": None,
    })
    store.insert_conversion({
        "record_id": inc, "converter": "datalab", "pdf_sha256": "deadbeef",
        "marker_version": "datalab-balanced", "config_hash": "h", "output_format": "markdown",
        "output_path": "markdown/abcd123456.datalab.md", "pages": 12, "used_llm": 0,
        "used_ocr": 0, "elapsed_ms": 0, "converted_at": "2026-05-18", "error": None,
    })
    return review, {"inc": inc, "exc": exc}


def test_export_included_writes_only_ft_included_with_provenance(tmp_path):
    review, ids = _setup(tmp_path)
    result = runner.invoke(app, ["export-included", str(review), "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["n_included"] == 1

    out = review / "exports" / "included_fulltext.csv"
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1
    r = rows[0]
    assert r["record_id"] == str(ids["inc"])
    assert r["title"] == "Included paper"
    assert r["authors"] == "Smith, J"
    # provenance + reasoning + paths
    assert r["doi"] == "10.1/a"
    assert r["contributing_sources"] == "openalex"
    assert r["ft_decision"] == "include"
    assert r["ft_reason_code"] == "I2"
    assert r["ft_reason_text"] == "meets all criteria"
    assert r["ft_screener"] == "anthropic:claude-sonnet-4-6"
    assert r["ta_reason_code"] == "I1"
    assert r["pdf_path"] == "pdfs/abcd123456.pdf"
    assert r["pdf_sha256"] == "deadbeef"
    assert r["pdf_resolver"] == "unpaywall"
    assert r["markdown_path"] == "markdown/abcd123456.datalab.md"
    assert r["converter"] == "datalab"
    assert r["pages"] == "12"


def test_export_included_empty_when_no_ft_decisions(tmp_path):
    review = tmp_path / "review"
    review.mkdir()
    ReviewFiles(review).init()
    result = runner.invoke(app, ["export-included", str(review), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["n_included"] == 0
    # header-only CSV
    out = review / "exports" / "included_fulltext.csv"
    assert out.exists()
    assert list(csv.DictReader(out.open())) == []
