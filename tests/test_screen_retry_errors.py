"""`slr screen --stage full_text --retry-errors` re-screens records whose PDF
conversion previously errored, instead of treating them as done on resume."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import slr.cli as cli  # noqa: F401  (kept for import parity)
import slr.fulltext.convert as convert_mod
import slr.screen.runner as runner_mod
from slr.cli import app
from slr.dedup import deduplicate_occurrences, plans_to_payload
from slr.fulltext.naming import target_basename_for_row
from slr.llm.base import Provider, ProviderResponse
from slr.record import Author, Record
from tests import ReviewFiles


runner = CliRunner()

_PROTOCOL = """\
review_id: test-retry
title: "Retry errored conversions"
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
    converter: marker
fetch:
  marker:
    isolate: false
"""


class _Provider(Provider):
    name = "anthropic"

    def complete(self, system, messages, model, max_tokens=1024, temperature=0.0,
                 response_format="json", attachments=None):
        return ProviderResponse(
            text='{"decision":"include","reason_code":"I1","reason_text":"ok"}',
            input_tokens=1, output_tokens=1,
        )


def _setup(tmp_path: Path) -> tuple[Path, int]:
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(_PROTOCOL)
    (review / "config.yaml").write_text(_CONFIG)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "q", "tq", [], "0.0.1", 1)
    store.insert_records(run_id, [Record(
        source="openalex", source_id="W1", doi="10.1/1",
        title="Novel CRISPR experiment", authors=[Author(family="A1")], year=2024,
    )])
    store.write_final_records(plans_to_payload(deduplicate_occurrences(store.all_records())))
    for s in ("search", "dedup", "screen_title_abstract", "fetch"):
        store.set_stage(s, "h", "h")

    record = store.final_records()[0]
    store.append_screening_decision("title_abstract", {
        "record_id": record["record_id"], "decision": "include",
        "reason_code": "I1", "reason_text": "x", "screener": "rules-v1",
    })
    (review / "pdfs").mkdir()
    (review / "pdfs" / target_basename_for_row(record)).write_bytes(b"%PDF-1.4 data")
    return review, record["record_id"]


def test_retry_errors_rescreens_failed_conversion(tmp_path, monkeypatch):
    review, record_id = _setup(tmp_path)
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _Provider())

    # --- Run 1: conversion fails. ---
    state = {"fail": True}

    def flaky(*a, **k):
        if state["fail"]:
            raise RuntimeError("Extend API request failed")
        return ("# real full text", 1)

    monkeypatch.setattr(convert_mod, "_run_marker", flaky)

    r1 = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    assert r1.exit_code == 0, r1.stdout + r1.stderr
    p1 = json.loads(r1.stdout)
    assert p1["n_conversion_errors"] == 1
    assert p1["counts"]["unsure"] == 1            # required fulltext missing -> unsure
    store = ReviewFiles(review)
    assert store.record_ids_with_conversion_error() == {record_id}

    # --- Run 2 WITHOUT retry: record already decided, skipped. ---
    r2 = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    p2 = json.loads(r2.stdout)
    assert p2["n_decided_this_run"] == 0          # resume treats it as done

    # --- Run 3 WITH --retry-errors and the engine now healthy. ---
    state["fail"] = False
    r3 = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--retry-errors", "--json"])
    assert r3.exit_code == 0, r3.stdout + r3.stderr
    p3 = json.loads(r3.stdout)
    assert p3["n_decided_this_run"] == 1
    assert p3["n_conversion_errors"] == 0
    store = ReviewFiles(review)
    ft = store.latest_decisions("full_text")
    assert [d["decision"] for d in ft] == ["include"]   # re-screened with real text
    assert store.record_ids_with_conversion_error() == set()


def test_retry_errors_noop_when_nothing_errored(tmp_path, monkeypatch):
    review, _record_id = _setup(tmp_path)
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _Provider())
    monkeypatch.setattr(convert_mod, "_run_marker", lambda *a, **k: ("# text", 1))

    runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    r = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--retry-errors", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["n_decided_this_run"] == 0   # nothing to retry
