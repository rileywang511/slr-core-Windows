"""Full-text evidence modes must select the right title/abstract subset."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import slr.screen.runner as runner_mod
from slr.cli import app
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.llm.base import Provider, ProviderResponse
from slr.record import Author, Record
from tests import ReviewFiles


runner = CliRunner()


_PROTOCOL = """\
review_id: test-ft-scope
title: "Full text scope test"
authors: ["Tester"]
created: 2026-05-18
query: TS=(crispr)
sources: [openalex]
inclusion_criteria:
  - { id: I1, text: "About CRISPR" }
exclusion_criteria: []
screen:
  title_abstract:
    type: rules
  full_text:
    type: llm
    fulltext: required
dedup:
  fuzzy_title_threshold: 90
"""

_CONFIG = """\
screen:
  full_text:
    provider: anthropic
    model: claude-sonnet-4-6
    converter: none
"""


class _RecordingProvider(Provider):
    """Captures the titles of every record actually sent to the model."""

    name = "anthropic"
    seen_titles: list[str] = []

    def complete(self, system, messages, model, max_tokens=1024, temperature=0.0,
                 response_format="json", attachments=None):
        type(self).seen_titles.append(messages[0].content.splitlines()[0])  # "Title: ..."
        return ProviderResponse(
            text='{"decision":"include","reason_code":"I1","reason_text":"ok"}',
            input_tokens=1, output_tokens=1,
        )


def _seed_pdf(review: Path, store: ReviewFiles, record: dict) -> None:
    """Write the standard readable PDF and record a successful attempt."""
    from slr.fulltext.naming import target_basename_for_row
    pdfs = review / "pdfs"
    pdfs.mkdir(exist_ok=True)
    name = target_basename_for_row(record)
    (pdfs / name).write_bytes(b"%PDF-1.4 fulltext")
    store.insert_fulltext_attempt({
        "record_id": record["record_id"], "source": "unpaywall", "url": "http://x",
        "http_status": 200, "bytes": 17, "pdf_sha256": "sha",
        "pdf_path": f"pdfs/{name}", "attempted_at": "2026-05-18", "error": None,
    })


def _setup(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(_PROTOCOL)
    (review / "config.yaml").write_text(_CONFIG)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 3)
    # Three distinct papers → three final records.
    store.insert_records(run_id, [
        Record(source="openalex", source_id="A", doi="10.1/a", title="Paper A passed with doc",
               authors=[Author(family="A")], year=2024),
        Record(source="openalex", source_id="B", doi="10.1/b", title="Paper B excluded at TA",
               authors=[Author(family="B")], year=2024),
        Record(source="openalex", source_id="C", doi="10.1/c", title="Paper C passed no doc",
               authors=[Author(family="C")], year=2024),
    ])
    store.write_final_records(plans_to_payload(deduplicate_occurrences(store.all_records())))
    for s in ("search", "dedup", "screen_title_abstract", "fetch"):
        store.set_stage(s, "h", "h")

    by_title = {record["title"]: record for record in store.final_records()}
    a = by_title["Paper A passed with doc"]
    b = by_title["Paper B excluded at TA"]
    c = by_title["Paper C passed no doc"]

    # TA decisions: A and C included, B excluded.
    for record, dec in [(a, "include"), (b, "exclude"), (c, "include")]:
        store.append_screening_decision("title_abstract", {
            "record_id": record["record_id"], "decision": dec, "reason_code": "I1",
            "reason_text": "x", "screener": "rules-v1",
        })

    # PDFs exist only for A and B (NOT for the passed-but-docless C).
    _seed_pdf(review, store, a)
    _seed_pdf(review, store, b)

    return review, {"A": a["record_id"], "B": b["record_id"], "C": c["record_id"]}


def test_full_text_only_screens_passed_ta_with_document(tmp_path, monkeypatch):
    review, ids = _setup(tmp_path)
    _RecordingProvider.seen_titles = []
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _RecordingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr

    # Only Paper A satisfies BOTH gates (passed TA + has a document).
    assert _RecordingProvider.seen_titles == ["Title: Paper A passed with doc"]

    parsed = json.loads(result.stdout)
    assert parsed["counts"]["include"] == 1

    # Exactly one full_text decision was recorded — for record A.
    store = ReviewFiles(review)
    ft = store.latest_decisions("full_text")
    assert [d["record_id"] for d in ft] == [ids["A"]]
    decision = ft[0]
    assert decision["model_id"] == "claude-sonnet-4-6"
    assert "SYSTEM:" in decision["prompt"]
    assert "Paper A passed with doc" in decision["prompt"]
    assert decision["response"].startswith('{"decision":"include"')
    assert decision["input_tokens"] == 1
    assert decision["output_tokens"] == 1
    assert isinstance(decision["latency_ms"], int)


def test_full_text_does_not_walk_whole_dataset(tmp_path, monkeypatch):
    """Regression: it must not iterate every record (B was excluded, C has no doc)."""
    review, ids = _setup(tmp_path)
    _RecordingProvider.seen_titles = []
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _RecordingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text"])
    assert result.exit_code == 0, result.stdout + result.stderr
    # 3 records total, but only 1 is eligible.
    assert "1 pending of 1" in result.stderr
    assert len(_RecordingProvider.seen_titles) == 1


def test_full_text_does_not_fall_back_when_ta_completed_with_no_includes(tmp_path, monkeypatch):
    review, _ids = _setup(tmp_path)
    from slr import frames

    frames.clear_stage_decisions(review, "title_abstract")
    _RecordingProvider.seen_titles = []
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _RecordingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert _RecordingProvider.seen_titles == []
    assert json.loads(result.stdout)["n_decided_this_run"] == 0


def test_optional_full_text_screens_passed_records_without_a_pdf(tmp_path, monkeypatch):
    review, ids = _setup(tmp_path)
    (review / "protocol.yaml").write_text(_PROTOCOL.replace("fulltext: required", "fulltext: optional"))
    _RecordingProvider.seen_titles = []
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _RecordingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert _RecordingProvider.seen_titles == [
        "Title: Paper A passed with doc", "Title: Paper C passed no doc",
    ]
    assert {d["record_id"] for d in ReviewFiles(review).latest_decisions("full_text")} == {
        ids["A"], ids["C"],
    }


def test_ignored_full_text_is_abstract_only_and_never_scans_pdfs(tmp_path, monkeypatch):
    review, _ids = _setup(tmp_path)
    (review / "protocol.yaml").write_text(_PROTOCOL.replace("fulltext: required", "fulltext: ignored"))
    _RecordingProvider.seen_titles = []
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _RecordingProvider())
    monkeypatch.setattr(
        runner_mod, "scan_for_manual_pdfs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ignored mode must not scan PDFs")),
    )

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert _RecordingProvider.seen_titles == [
        "Title: Paper A passed with doc", "Title: Paper C passed no doc",
    ]
