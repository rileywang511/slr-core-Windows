"""Full-text screening with converter: none — the raw PDF is sent to the model."""

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
review_id: test-ft-pdf
title: "PDF-direct full text test"
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
    rules:
      - { action: include, field: title, pattern: '\\bcrispr\\b', reason_code: I1, reason_text: "crispr" }
  full_text:
    type: llm
    fulltext: optional
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


class _CapturingProvider(Provider):
    name = "anthropic"
    last_attachments = None
    last_user = None

    def complete(self, system, messages, model, max_tokens=1024, temperature=0.0,
                 response_format="json", attachments=None):
        type(self).last_attachments = attachments
        type(self).last_user = messages[0].content
        return ProviderResponse(
            text='{"decision":"include","reason_code":"I1","reason_text":"ok","confidence":0.9}',
            input_tokens=5, output_tokens=5,
        )


def _setup(tmp_path: Path) -> Path:
    review = tmp_path / "review"
    review.mkdir()
    (review / "protocol.yaml").write_text(_PROTOCOL)
    (review / "config.yaml").write_text(_CONFIG)
    store = ReviewFiles(review)
    store.init()
    run_id = store.insert_source_run("openalex", "TS=(crispr)", "tq", [], "0.0.1", 1)
    store.insert_records(run_id, [Record(
        source="openalex", source_id="W1", doi="10.1/1",
        title="Novel CRISPR experiment", authors=[Author(family="A1")], year=2024,
    )])
    rows = store.all_records()
    store.write_final_records(plans_to_payload(deduplicate_occurrences(rows)))
    for s in ("search", "dedup", "fetch"):
        store.set_stage(s, "h", "h")

    # Seed a successful PDF download for the single record.
    record_id = store.final_records()[0]["record_id"]
    pdfs_dir = review / "pdfs"
    pdfs_dir.mkdir()
    (pdfs_dir / "deadbeef.pdf").write_bytes(b"%PDF-1.4 fake")
    store.insert_fulltext_attempt({
        "record_id": record_id, "source": "unpaywall", "url": "http://x",
        "http_status": 200, "bytes": 12, "pdf_sha256": "sha",
        "pdf_path": "pdfs/deadbeef.pdf", "attempted_at": "2026-05-18", "error": None,
    })
    return review


def test_full_text_pdf_mode_sends_pdf_and_skips_convert(tmp_path, monkeypatch):
    review = _setup(tmp_path)
    _CapturingProvider.last_attachments = None
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _CapturingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr

    parsed = json.loads(result.stdout)
    assert parsed["counts"]["include"] == 1

    # The provider received the raw PDF as an attachment — no convert step ran.
    atts = _CapturingProvider.last_attachments
    assert atts is not None and len(atts) == 1
    assert atts[0].filename == "deadbeef.pdf"
    assert atts[0].data == b"%PDF-1.4 fake"
    # No markdown was ever produced.
    assert not (review / "markdown").exists()


def test_pdf_resolved_by_canonical_name_when_recorded_path_is_stale(tmp_path, monkeypatch):
    """Prefer the canonical readable name over a stale recorded path."""
    from slr.fulltext.naming import target_basename_for_row

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
    for s in ("search", "dedup", "fetch"):
        store.set_stage(s, "h", "h")

    record = store.final_records()[0]
    short_name = target_basename_for_row(record)
    (review / "pdfs").mkdir()
    (review / "pdfs" / short_name).write_bytes(b"%PDF-1.4 short")

    # The evidence records a path that no longer exists on disk.
    stale = "pdfs/" + "e" * 64 + ".pdf"
    store.insert_fulltext_attempt({
        "record_id": record["record_id"], "source": "unpaywall", "url": "http://x",
        "http_status": 200, "bytes": 12, "pdf_sha256": "sha",
        "pdf_path": stale, "attempted_at": "2026-05-18", "error": None,
    })

    _CapturingProvider.last_attachments = None
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _CapturingProvider())
    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr

    atts = _CapturingProvider.last_attachments
    assert atts is not None and len(atts) == 1
    # Resolved to the canonical file, not the stale recorded path.
    assert atts[0].filename == short_name
    assert short_name.endswith("--r1.pdf")
    assert atts[0].data == b"%PDF-1.4 short"


def test_full_text_marker_mode_feeds_text_not_pdf(tmp_path, monkeypatch):
    review = _setup(tmp_path)
    # Use converter: marker (in-process) and stub the heavy marker engine.
    (review / "config.yaml").write_text(
        "screen:\n  full_text:\n    provider: anthropic\n    model: claude-sonnet-4-6\n    converter: marker\n"
        "fetch:\n  marker:\n    isolate: false\n"
    )
    import slr.fulltext.convert as convert_mod
    monkeypatch.setattr(convert_mod, "_run_marker", lambda *a, **k: ("# Converted body text", 2))
    _CapturingProvider.last_attachments = None
    _CapturingProvider.last_user = None
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _CapturingProvider())

    result = runner.invoke(app, ["screen", str(review), "--stage", "full_text", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    # marker mode: converted text is fed as context, NOT attached as a PDF.
    assert _CapturingProvider.last_attachments is None
    assert "Converted body text" in _CapturingProvider.last_user
    # The conversion was cached to markdown/<stem>.marker.md.
    assert list((review / "markdown").glob("*.marker.md"))


def test_converter_flag_overrides_config(tmp_path, monkeypatch):
    """--converter none overrides a config that says marker."""
    review = _setup(tmp_path)
    (review / "config.yaml").write_text(
        "screen:\n  full_text:\n    provider: anthropic\n    model: claude-sonnet-4-6\n    converter: marker\n"
    )
    _CapturingProvider.last_attachments = None
    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: _CapturingProvider())

    result = runner.invoke(
        app, ["screen", str(review), "--stage", "full_text", "--converter", "none", "--json"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    # Overridden to none -> raw PDF attached, no markdown produced.
    assert _CapturingProvider.last_attachments is not None
    assert not (review / "markdown").exists()
    assert json.loads(result.stdout)["converter"] == "none"
