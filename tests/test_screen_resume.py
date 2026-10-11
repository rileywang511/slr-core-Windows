"""Resume / restart behaviour for `slr screen`.

The screener must:
  1. Append each decision to its CSV immediately (so a crash loses at most the
     in-flight item).
  2. On re-invocation, skip records that already have a decision and resume
     from where it left off.
  3. With --restart, wipe existing decisions for the stage and re-screen
     everything from scratch.
"""

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
review_id: t
title: "Resume test"
authors: ["T"]
created: 2026-05-18
query: TS=(x)
sources: [openalex]
inclusion_criteria: [{ id: I1, text: "x" }]
exclusion_criteria: [{ id: E1, text: "y" }]
screen:
  title_abstract:
    type: rules
    rules:
      - { action: include, field: any, pattern: '.', reason_code: I1, reason_text: "match" }
  full_text:
    type: manual
dedup:
  fuzzy_title_threshold: 90
"""


def _seed(tmp_path: Path, n_records: int = 8) -> Path:
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(_RULES_PROTOCOL)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "TS=(x)", "tq", [], "0.0.1", n_records)
    recs = [
        Record(source="openalex", source_id=f"W{i}", doi=f"10.1/{i}",
               title=f"Paper {i}", authors=[Author(family=f"A{i}")], year=2024)
        for i in range(n_records)
    ]
    store.insert_records(run_id, recs)
    plans = deduplicate_occurrences(store.all_records())
    store.write_final_records(plans_to_payload(plans))
    store.set_stage("search", "h", "h")
    store.set_stage("dedup", "h", "h")
    return review


def test_decisions_persisted_one_at_a_time(tmp_path: Path, monkeypatch):
    """Inject a screener that crashes after N successful decisions and verify
    those N decisions are already in the decision CSV when we look."""
    review = _seed(tmp_path, n_records=8)

    from slr.screen.rules import RulesScreener
    call_count = {"n": 0}
    real_decide = RulesScreener.decide

    def crashy_decide(self, record, fulltext=None):
        call_count["n"] += 1
        if call_count["n"] == 5:
            raise SystemExit("simulated process kill mid-run")
        return real_decide(self, record, fulltext=fulltext)

    monkeypatch.setattr(RulesScreener, "decide", crashy_decide)

    # Run — it'll bail out at item 5
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--quiet"])
    # SystemExit propagates as a non-zero exit
    assert result.exit_code != 0

    # The first 4 successful decisions should already be in the CSV.
    store = ReviewFiles(review)
    decisions = store.latest_decisions("title_abstract")
    assert len(decisions) == 4
    # Counts already aggregable
    assert store.decision_counts("title_abstract")["include"] == 4


def test_resume_skips_already_decided(tmp_path: Path):
    review = _seed(tmp_path, n_records=8)
    # First run: complete all 8
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--quiet", "--json"])
    assert result.exit_code == 0
    payload1 = json.loads(result.stdout)
    assert payload1["n_decided_this_run"] == 8

    # Second run: every record is already decided, should run zero new decisions.
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--quiet", "--json"])
    assert result.exit_code == 0
    payload2 = json.loads(result.stdout)
    assert payload2["n_decided_this_run"] == 0
    # Cumulative counts still reflect the 8 from the first run
    assert payload2["counts"]["include"] == 8

    # No duplicate decisions (one row per record).
    store = ReviewFiles(review)
    decisions = store.latest_decisions("title_abstract")
    assert len(decisions) == 8


def test_resume_after_partial_run(tmp_path: Path, monkeypatch):
    """First run crashes; second run picks up the rest."""
    review = _seed(tmp_path, n_records=8)

    from slr.screen.rules import RulesScreener
    call_count = {"n": 0}
    real_decide = RulesScreener.decide

    def crashy(self, record, fulltext=None):
        call_count["n"] += 1
        if call_count["n"] == 4:
            raise SystemExit("kill")
        return real_decide(self, record, fulltext=fulltext)

    monkeypatch.setattr(RulesScreener, "decide", crashy)
    runner.invoke(app, ["screen", str(review),
                        "--stage", "title_abstract", "--quiet"])

    store = ReviewFiles(review)
    n_partial = len(store.record_ids_with_decisions("title_abstract"))
    assert n_partial == 3  # crashed on the 4th

    # Reset patch, run resume
    monkeypatch.setattr(RulesScreener, "decide", real_decide)
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract", "--quiet", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["n_decided_this_run"] == 5     # the remaining ones
    assert payload["counts"]["include"] == 8      # cumulative
    assert len(ReviewFiles(review).latest_decisions("title_abstract")) == 8


def test_restart_wipes_existing_decisions(tmp_path: Path):
    review = _seed(tmp_path, n_records=4)
    # Initial full run
    runner.invoke(app, ["screen", str(review),
                        "--stage", "title_abstract", "--quiet"])
    assert ReviewFiles(review).decision_counts("title_abstract")["include"] == 4

    # --restart wipes the slate
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract",
                                 "--restart", "--quiet", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["n_decided_this_run"] == 4   # all 4 re-screened
    # And there are still only 4 decisions (no duplicates from the prior run)
    assert len(ReviewFiles(review).latest_decisions("title_abstract")) == 4


def test_changed_prompt_methodology_invalidates_existing_decisions(tmp_path: Path):
    review = _seed(tmp_path, n_records=2)
    first = runner.invoke(app, ["screen", str(review), "--stage", "title_abstract", "--quiet", "--json"])
    assert first.exit_code == 0
    assert json.loads(first.stdout)["n_decided_this_run"] == 2

    protocol_path = review / "protocol.yaml"
    protocol_path.write_text(_RULES_PROTOCOL.replace('text: "x"', 'text: "changed methodology"'))
    second = runner.invoke(app, ["screen", str(review), "--stage", "title_abstract", "--quiet", "--json"])

    assert second.exit_code == 0
    assert json.loads(second.stdout)["n_decided_this_run"] == 2


def test_progress_seeded_from_existing_decisions(tmp_path: Path):
    """Resume run should emit a progress line that already reflects the
    cumulative include count, not 0."""
    review = _seed(tmp_path, n_records=8)
    # First run: do all 8
    runner.invoke(app, ["screen", str(review),
                        "--stage", "title_abstract", "--quiet"])
    # Second run (everything already decided) — confirm the announcement says so
    result = runner.invoke(app, ["screen", str(review),
                                 "--stage", "title_abstract"])
    assert result.exit_code == 0
    assert "8 already decided" in result.stderr
    assert "0 pending" in result.stderr
