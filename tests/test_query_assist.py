"""freetext_to_query: a generated query must round-trip through the parser."""

from __future__ import annotations

from slr.llm.base import Message, Provider, ProviderResponse, ProviderSpec
from slr.query import parse
from slr.query.assist import freetext_to_query


class _StubProvider(Provider):
    """Returns a canned completion; records the last call for assertions."""

    name = "stub"

    def __init__(self, text: str):
        self._text = text
        self.last_system: str | None = None
        self.last_messages: list[Message] | None = None

    def complete(self, system, messages, model, max_tokens=1024, temperature=0.0,
                 response_format="json", attachments=None):
        self.last_system = system
        self.last_messages = messages
        return ProviderResponse(text=self._text)


_SPEC = ProviderSpec(provider="anthropic", model="stub-model")


def test_clean_query_is_valid_and_parses():
    q = 'TS=("antibiotic resistance" OR "AMR") AND PY=2015-2026'
    prov = _StubProvider(q)
    result = freetext_to_query("AMR in water since 2015", _SPEC, provider=prov)
    assert result["valid"] is True
    assert result["parse_error"] is None
    assert result["query"] == q
    parse(result["query"])  # must not raise


def test_strips_code_fences_and_label():
    prov = _StubProvider('Query: ```\nTS=("metagenome*")\n```')
    result = freetext_to_query("metagenomes", _SPEC, provider=prov)
    assert result["query"] == 'TS=("metagenome*")'
    assert result["valid"] is True


def test_invalid_query_reports_parse_error_not_raises():
    prov = _StubProvider('TS=("unterminated')
    result = freetext_to_query("broken", _SPEC, provider=prov)
    assert result["valid"] is False
    assert result["parse_error"]


def test_description_is_passed_to_model():
    prov = _StubProvider("TS=(crispr)")
    freetext_to_query("studies about CRISPR off-target", _SPEC, provider=prov)
    assert prov.last_messages is not None
    assert "CRISPR" in prov.last_messages[0].content
