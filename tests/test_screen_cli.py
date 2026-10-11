from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from slr.cli import app
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.record import Author, Record
from tests import ReviewFiles


runner = CliRunner()


_RULES_PROTOCOL = """\
review_id: test-screen
title: "Screening progress test"
authors: ["Tester"]
created: 2026-05-18
query: TS=(crispr)
sources: [openalex]
inclusion_criteria:
  - { id: I1, text: "Has 'crispr' in title" }
exclusion_criteria:
  - { id: E1, text: "Review or editorial" }
screen:
  title_abstract:
    type: rules
    rules:
      - { action: exclude, field: title, pattern: '\\breview\\b',    reason_code: E1, reason_text: "review" }
      - { action: include, field: title, pattern: '\\bcrispr\\b',    reason_code: I1, reason_text: "crispr" }
  full_text:
    type: manual
dedup:
  fuzzy_title_threshold: 90
"""


def _setup_review(tmp_path: Path, n_records: int = 6) -> Path:
    """Seed a review dir with `n_records` final records ready to screen."""
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(_RULES_PROTOCOL)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "TS=(crispr)", "tq", [], "0.0.1", n_records)
    recs: list[Record] = []
    for i in range(n_records):
        title = "A review of CRISPR" if i % 3 == 0 else "Novel CRISPR experiment"
        recs.append(Record(
            source="openalex", source_id=f"W{i}", doi=f"10.1/{i}",
            title=title,
            authors=[Author(family=f"A{i}")],
            year=2024,
        ))
    store.insert_records(run_id, recs)
    rows = store.all_records()
    plans = deduplicate_occurrences(rows)
    store.write_final_records(plans_to_payload(plans))
    store.set_stage("search", "h", "h")
    store.set_stage("dedup", "h", "h")
    return review


def test_screen_emits_progress_to_stderr(tmp_path: Path):
    review = _setup_review(tmp_path, n_records=6)
    result = runner.invoke(app, ["screen", str(review), "--stage", "title_abstract"],
                           )
    assert result.exit_code == 0, result.stdout + result.stderr
    # stderr should contain per-item tally lines + the final summary
    assert "screening" in result.stderr and "6" in result.stderr
    assert "title_abstract:" in result.stderr
    assert "done — include" in result.stderr
    # The tally line includes counts
    assert "include" in result.stderr and "exclude" in result.stderr and "unsure" in result.stderr


def test_screen_quiet_suppresses_stderr(tmp_path: Path):
    review = _setup_review(tmp_path, n_records=4)
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--quiet"],
                           )
    assert result.exit_code == 0, result.stdout + result.stderr
    # No progress chatter
    assert "screening" not in result.stderr
    assert "done —" not in result.stderr


def test_screen_json_keeps_stdout_clean(tmp_path: Path):
    review = _setup_review(tmp_path, n_records=4)
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--json"],
                           )
    assert result.exit_code == 0, result.stdout + result.stderr
    # stdout is JSON only — must parse
    parsed = json.loads(result.stdout)
    assert parsed["stage"] == "title_abstract"
    assert "counts" in parsed
    # stderr has the progress
    assert "screening" in result.stderr


def test_screen_recovers_from_screener_errors(tmp_path: Path, monkeypatch):
    """If a screener raises mid-loop, screening continues with 'unsure' + audit
    records the error."""
    review = _setup_review(tmp_path, n_records=5)

    from slr.screen.rules import RulesScreener
    real_decide = RulesScreener.decide
    call_count = {"n": 0}

    def flaky_decide(self, record, fulltext=None):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated transient blow-up")
        return real_decide(self, record, fulltext=fulltext)

    monkeypatch.setattr(RulesScreener, "decide", flaky_decide)

    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--json"],
                           )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["counts"]["include"] + parsed["counts"]["exclude"] + parsed["counts"]["unsure"] == 5
    assert parsed["n_errors"] == 1
    # The failed record should be recorded as 'unsure' with reason SCREENER_ERROR.
    store = ReviewFiles(review)
    decisions = store.latest_decisions("title_abstract")
    err_decisions = [d for d in decisions if d["reason_code"] == "SCREENER_ERROR"]
    assert len(err_decisions) == 1
    # And the audit log records "status": "partial"
    audit_lines = (review / "audit.log.jsonl").read_text().splitlines()
    screen_entry = json.loads(audit_lines[-1])
    assert screen_entry["command"] == "screen"
    assert screen_entry["status"] == "partial"
    assert screen_entry["details"]["n_errors"] == 1
