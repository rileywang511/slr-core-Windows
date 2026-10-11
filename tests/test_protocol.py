from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from slr.protocol import EXAMPLE_PROTOCOL, Protocol, load_protocol


def test_load_example_protocol(tmp_path: Path):
    p = tmp_path / "protocol.yaml"
    p.write_text(EXAMPLE_PROTOCOL, encoding="utf-8")
    proto = load_protocol(p)
    assert proto.review_id == "example-2026"
    assert "openalex" in proto.sources
    assert "europepmc" in proto.sources
    assert proto.screen.title_abstract.type == "llm"
    assert proto.dedup.fuzzy_title_threshold == 90


def test_protocol_hash_is_deterministic(tmp_path: Path):
    p = tmp_path / "protocol.yaml"
    p.write_text(EXAMPLE_PROTOCOL, encoding="utf-8")
    h1 = load_protocol(p).hash()
    h2 = load_protocol(p).hash()
    assert h1 == h2


def test_protocol_rejects_inert_fields_and_duplicate_criterion_ids():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Protocol.model_validate({
            "review_id": "r", "title": "t", "query": "TS=(x)", "sources": ["openalex"],
            "typo_that_would_be_ignored": True,
        })
    with pytest.raises(ValidationError, match="criterion IDs must be unique"):
        Protocol.model_validate({
            "review_id": "r", "title": "t", "query": "TS=(x)", "sources": ["openalex"],
            "inclusion_criteria": [{"id": "I1", "text": "one"}],
            "exclusion_criteria": [{"id": "I1", "text": "two"}],
        })


def test_retired_screening_heading_is_rejected():
    with pytest.raises(ValidationError, match="screening"):
        Protocol.model_validate({
            "review_id": "r", "title": "t", "query": "TS=(x)",
            "sources": ["openalex"], "screening": {},
        })
