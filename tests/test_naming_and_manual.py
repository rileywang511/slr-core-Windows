"""Predictable PDF filenames and the manual-collection ingest workflow."""

from __future__ import annotations

import csv
from pathlib import Path

import httpx

from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.fulltext.naming import is_target_filename, target_basename
from slr.fulltext.scan_manual import scan_for_manual_pdfs
from slr.fulltext.unresolved import write_unresolved_csv
from slr.record import Author, Record
from tests import ReviewFiles

_PDF = b"%PDF-1.4\nfake\n%%EOF"


def test_target_basename_is_stable_and_readable():
    a = target_basename(record_id=5, doi="10.1/X", title="Hello", year=2024)
    b = target_basename(record_id=5, doi="10.1/X", title="Hello", year=2024)
    assert a == b   # deterministic
    assert a.endswith(".pdf")
    assert a == "2024-hello--r5.pdf"
    assert is_target_filename(a)


def test_target_basename_normalises_doi_case():
    a = target_basename(record_id=1, doi="10.1/AbC", title=None, year=None)
    b = target_basename(record_id=1, doi="10.1/abc", title=None, year=None)
    assert a == b


def test_target_basename_distinct_records():
    a = target_basename(record_id=1, doi="10.1/x", title="t", year=2024)
    b = target_basename(record_id=2, doi="10.1/x", title="t", year=2024)
    assert a != b   # record ID makes otherwise-identical names collision-safe


def test_target_basename_no_doi_falls_back_to_title():
    a = target_basename(record_id=1, doi=None, title="A paper", year=2024)
    b = target_basename(record_id=1, doi=None, title="A paper", year=2024)
    assert a == b
    assert is_target_filename(a)


def _seed(tmp_path: Path, n_records: int = 3) -> tuple[ReviewFiles, Path]:
    review = tmp_path / "r"
    review.mkdir()
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "q", "t", [], "0.0.1", n_records)
    recs = [
        Record(source="openalex", source_id=f"W{i}", doi=f"10.1/p{i}",
               title=f"Paper {i}", authors=[Author(family=f"A{i}")], year=2024,
               venue="J")
        for i in range(n_records)
    ]
    store.insert_records(run_id, recs)
    plans = deduplicate_occurrences(store.all_records())
    store.write_final_records(plans_to_payload(plans))
    return store, review


def test_unresolved_csv_has_save_as_column(tmp_path: Path):
    store, review = _seed(tmp_path)
    # Simulate fetch having tried and failed for final record 1.
    record_ids = sorted(record["record_id"] for record in store.final_records())
    target_record_id = record_ids[0]
    store.insert_fulltext_attempt({
        "record_id": target_record_id, "source": "unpaywall", "url": None, "http_status": 404,
        "bytes": 0, "pdf_sha256": None, "pdf_path": None,
        "attempted_at": "2026-05-18T00:00:00Z", "error": "not found",
    })
    out = review / "unresolved.csv"
    n = write_unresolved_csv(review, out)
    assert n == 1
    with open(out) as f:
        rows = list(csv.DictReader(f))
    assert "save_as" in rows[0]
    # save_as should match what target_basename would compute for that record.
    record_rows = {record["record_id"]: record for record in store.final_records()}
    expected = target_basename(
        record_id=target_record_id,
        doi=record_rows[target_record_id]["doi"],
        title=record_rows[target_record_id]["title"],
        year=record_rows[target_record_id]["year"],
    )
    assert rows[0]["save_as"] == expected
    assert is_target_filename(rows[0]["save_as"])


def test_scan_for_manual_pdfs_ingests_user_files(tmp_path: Path):
    store, review = _seed(tmp_path, n_records=3)
    pdfs_dir = review / "pdfs"
    pdfs_dir.mkdir()

    # User saves PDFs for records 1 and 2 (using the names we'd tell them)
    record_rows = {record["record_id"]: record for record in store.final_records()}
    record_ids = sorted(record_rows)
    for record_id in record_ids[:2]:
        record = record_rows[record_id]
        name = target_basename(record_id=record_id, doi=record["doi"], title=record["title"], year=record["year"])
        (pdfs_dir / name).write_bytes(_PDF)

    # Record 3 has nothing.
    result = scan_for_manual_pdfs(review, "pdfs/")
    assert result == {"ingested": 2, "skipped_existing": 0, "unrecognised": 0}

    # Re-running is idempotent (now those records have a successful row)
    result2 = scan_for_manual_pdfs(review, "pdfs/")
    assert result2 == {"ingested": 0, "skipped_existing": 2, "unrecognised": 0}


def test_scan_skips_non_pdf_content_even_with_correct_name(tmp_path: Path):
    """User pastes an HTML landing page; we don't ingest junk."""
    store, review = _seed(tmp_path, n_records=1)
    pdfs_dir = review / "pdfs"
    pdfs_dir.mkdir()
    record = store.final_records()[0]
    name = target_basename(record_id=record["record_id"], doi=record["doi"],
                           title=record["title"], year=record["year"])
    (pdfs_dir / name).write_bytes(b"<html>not a pdf</html>")
    result = scan_for_manual_pdfs(review, "pdfs/")
    assert result == {"ingested": 0, "skipped_existing": 0, "unrecognised": 0}


def test_scan_ignores_unrecognised_filenames(tmp_path: Path):
    """A user dropping `my-cool-paper.pdf` shouldn't crash or be silently ingested."""
    store, review = _seed(tmp_path, n_records=1)
    pdfs_dir = review / "pdfs"
    pdfs_dir.mkdir()
    (pdfs_dir / "my-cool-paper.pdf").write_bytes(_PDF)
    result = scan_for_manual_pdfs(review, "pdfs/")
    # Not matching the record-ID pattern at all — silently ignored.
    assert result == {"ingested": 0, "skipped_existing": 0, "unrecognised": 0}


def test_acquirer_saves_to_target_filename(tmp_path: Path):
    """Auto-downloaded PDFs land at the same predictable name as manual ones."""
    from slr.config import FetchConfig
    from slr.fulltext.acquire import Acquirer

    store, review = _seed(tmp_path, n_records=1)
    # Add source payload data that an OpenAlex resolver could use.
    record = store.final_records()[0]
    record_with_raw = dict(record)
    record_with_raw["raw_json"] = '{"source":"openalex"}'

    def handler(req):
        url = str(req.url)
        if "api.unpaywall.org" in url:
            return httpx.Response(200, json={
                "best_oa_location": {"url_for_pdf": "https://example.org/foo.pdf"},
            })
        if "example.org/foo.pdf" in url:
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=_PDF)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    acquirer = Acquirer(
        review_dir=review,
        config=FetchConfig(sources=["unpaywall"]),
        contact_email="me@example.org",
        http_client=client,
    )
    attempts = acquirer.acquire_one(record_with_raw)
    success = next(a for a in attempts if a.pdf_sha256)
    # The file at success.pdf_path has the predictable name
    expected_name = target_basename(
        record_id=record["record_id"], doi=record["doi"],
        title=record["title"], year=record["year"],
    )
    assert success.pdf_path.endswith(expected_name)
    assert (review / success.pdf_path).exists()
