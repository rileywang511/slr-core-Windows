from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from slr.cli import app

runner = CliRunner()


_MIN_PROTOCOL = """\
review_id: t
title: "T"
authors: ["T"]
created: 2026-05-18
query: TS=(x)
sources: [openalex]
inclusion_criteria: [{ id: I1, text: "x" }]
exclusion_criteria: [{ id: E1, text: "y" }]
screen:
  title_abstract: { type: llm }
  full_text: { type: manual }
"""


def _seed(tmp_path: Path, config_yaml: str | None) -> Path:
    review = tmp_path / "r"
    review.mkdir()
    (review / "protocol.yaml").write_text(_MIN_PROTOCOL)
    if config_yaml is not None:
        (review / "config.yaml").write_text(config_yaml)
    return review


def test_llm_test_fails_when_provider_not_configured(tmp_path: Path):
    review = _seed(tmp_path, None)
    result = runner.invoke(app, ["llm", "test", str(review), "--json"])
    assert result.exit_code == 1
    assert "provider" in result.stderr or "provider" in result.stdout


def test_llm_test_pings_fake_provider(tmp_path: Path, monkeypatch):
    """Patch the LLM factory so we don't talk to a real endpoint, and verify
    `slr llm test` correctly probes the stage and reports a parseable decision."""
    review = _seed(tmp_path, """\
search:
  contact_email: me@example.com
screen:
  title_abstract:
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    model: qwen2.5:7b
""")

    from slr.llm.base import Provider, ProviderResponse
    import slr.screen.runner as runner_mod

    class FakeProvider(Provider):
        name = "openai-compatible"
        def complete(self, system, messages, model, max_tokens=1024,
                     temperature=0.0, response_format="json"):
            assert "off-target" in messages[0].content   # the probe fixture
            return ProviderResponse(
                text='{"decision":"include","reason_code":"I1",'
                     '"reason_text":"probe ok","confidence":0.9}',
                input_tokens=42, output_tokens=12,
            )

    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: FakeProvider())

    result = runner.invoke(app, ["llm", "test", str(review), "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["provider"] == "openai-compatible"
    assert payload["model"] == "qwen2.5:7b"
    assert payload["base_url"] == "http://localhost:11434/v1"
    assert payload["stage"] == "title_abstract"
    assert payload["decision"] == "include"
    assert payload["reason_code"] == "I1"
    assert payload["latency_ms"] >= 0


def test_llm_test_reports_unparseable_response_with_hint(tmp_path: Path, monkeypatch):
    review = _seed(tmp_path, """\
screen:
  title_abstract:
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    model: junk-model
""")
    from slr.llm.base import Provider, ProviderResponse
    import slr.screen.runner as runner_mod

    class GarbageProvider(Provider):
        name = "openai-compatible"
        def complete(self, *a, **k):
            return ProviderResponse(text="thinking... I'm a teapot")

    monkeypatch.setattr(runner_mod, "make_provider", lambda spec: GarbageProvider())

    result = runner.invoke(app, ["llm", "test", str(review), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["reason_code"] == "PARSE_ERROR"
    assert "hint" in payload
    assert "JSON" in payload["hint"] or "json" in payload["hint"]
