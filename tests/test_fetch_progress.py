"""Verbose progress output, status table, and unresolved.csv for `slr fetch`."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from slr.cli import app
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.record import Author, Record
from tests import ReviewFiles

runner = CliRunner()


_PDF = b"%PDF-1.4\nfake\n%%EOF"
_HTML = b"<!doctype html><body>nope</body>"


_PROTOCOL = """\
review_id: fetch-test
title: "Fetch test"
authors: ["T"]
created: 2026-05-18
query: TS=(x)
sources: [openalex]
inclusion_criteria: [{ id: I1, text: "x" }]
exclusion_criteria: [{ id: E1, text: "y" }]
screen:
  title_abstract: { type: manual }
  full_text: { type: manual }
dedup:
  fuzzy_title_threshold: 90
"""

_CONFIG = """\
search:
  contact_email: me@example.org
fetch:
  sources: [unpaywall]
"""


def _seed(tmp_path: Path, n_records: int = 4) -> Path:
    review = tmp_path / "r"
    review.mkdir()
    (review / "protocol.yaml").write_text(_PROTOCOL)
    (review / "config.yaml").write_text(_CONFIG)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "TS=(x)", "t", [], "0.0.1", n_records)
    recs = [
        Record(source="openalex", source_id=f"W{i}", doi=f"10.1/p{i}",
               title=f"Paper {i}", authors=[Author(family=f"A{i}")], year=2024,
               venue="Test Journal")
        for i in range(n_records)
    ]
    store.insert_records(run_id, recs)
    plans = deduplicate_occurrences(store.all_records())
    store.write_final_records(plans_to_payload(plans))
    store.set_stage("search", "h", "h")
    store.set_stage("dedup", "h", "h")
    return review


def _make_transport(success_dois: set[str]):
    """Mock httpx: unpaywall finds OA for DOIs in `success_dois`; others 404."""
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "api.unpaywall.org" in url:
            for doi in success_dois:
                if doi in url:
                    return httpx.Response(200, json={
                        "best_oa_location": {"url_for_pdf": f"https://example.org/{doi}.pdf"},
                    })
            return httpx.Response(404)
        # PDF download
        if "example.org" in url:
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=_PDF)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_fetch_emits_progress_and_status_table(tmp_path: Path, monkeypatch):
    review = _seed(tmp_path, n_records=4)
    transport = _make_transport({"10.1/p0", "10.1/p2"})  # 2 of 4 succeed

    # Patch httpx.Client default to use our mock transport
    from slr.fulltext import acquire as acquire_mod
    real_client = httpx.Client
    monkeypatch.setattr(acquire_mod.httpx, "Client",
                        lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    result = runner.invoke(app, ["fetch", str(review)])
    assert result.exit_code == 0, result.stdout + result.stderr

    # Stderr should show the per-record progress.
    assert "[fetch] attempting" in result.stderr
    assert "fetch: done" in result.stderr
    assert "downloaded 2" in result.stderr
    assert "missing 2" in result.stderr

    # Stdout should show the status table + manual-collection note
    assert "total papers attempted" in result.stdout
    assert "2" in result.stdout  # downloaded count
    assert "unresolved.csv" in result.stdout
    assert "need manual collection" in result.stdout


def test_unresolved_csv_written_with_doi_links(tmp_path: Path, monkeypatch):
    review = _seed(tmp_path, n_records=3)
    transport = _make_transport({"10.1/p0"})  # 1 success, 2 missing

    from slr.fulltext import acquire as acquire_mod
    real_client = httpx.Client
    monkeypatch.setattr(acquire_mod.httpx, "Client",
                        lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    result = runner.invoke(app, ["fetch", str(review), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["n_total"] == 3
    assert payload["n_downloaded"] == 1
    assert payload["n_missing"] == 2
    assert payload["unresolved_csv"] is not None

    # The CSV should list the two missing papers with their DOI links
    with open(payload["unresolved_csv"]) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    dois = {r["doi"] for r in rows}
    assert dois == {"10.1/p1", "10.1/p2"}
    assert all(r["doi_url"].startswith("https://doi.org/") for r in rows)
    assert all(r["title"].startswith("Paper") for r in rows)
    assert all(r["first_author"].startswith("A") for r in rows)


def test_field_names_clear(tmp_path: Path, monkeypatch):
    """Output uses the new, clearer field names."""
    review = _seed(tmp_path, n_records=2)
    transport = _make_transport(set())  # all fail

    from slr.fulltext import acquire as acquire_mod
    real_client = httpx.Client
    monkeypatch.setattr(acquire_mod.httpx, "Client",
                        lambda **kw: real_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}))

    result = runner.invoke(app, ["fetch", str(review), "--json"])
    payload = json.loads(result.stdout)
    # New names
    assert "n_total" in payload
    assert "n_downloaded" in payload
    assert "n_missing" in payload
    assert "n_resolver_calls" in payload
    # Old confusing names should be gone
    assert "n_records_attempted" not in payload
    assert "n_attempts" not in payload
    assert "n_pdfs_downloaded" not in payload


def test_fetch_does_not_fall_back_when_ta_completed_with_no_includes(tmp_path: Path):
    review = _seed(tmp_path, n_records=2)
    store = ReviewFiles(review)
    store.set_stage("screen_title_abstract", "h", "h")

    result = runner.invoke(app, ["fetch", str(review), "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["n_total"] == 0
    assert payload["n_resolver_calls"] == 0
    assert payload["scope"] == "records included at title/abstract screening"
