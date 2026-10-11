from __future__ import annotations

from slr.llm.base import Provider, ProviderResponse
from slr.protocol import Criterion
from slr.screen.llm import LLMScreener, _parse_decision
from slr.screen.rules import RulesScreener


def test_rules_excludes_review():
    rules = [
        {"action": "exclude", "field": "title", "pattern": r"\breview\b",
         "reason_code": "E1", "reason_text": "Review"},
    ]
    s = RulesScreener(rules=rules)
    d = s.decide({"record_id": 7, "title": "A systematic review of foo", "abstract": ""})
    assert d.decision == "exclude"
    assert d.reason_code == "E1"


def test_rules_unsure_on_no_match():
    s = RulesScreener(rules=[
        {"action": "exclude", "field": "title", "pattern": r"\bnonsense\b",
         "reason_code": "E1", "reason_text": "no"},
    ])
    d = s.decide({"record_id": 1, "title": "ordinary paper", "abstract": ""})
    assert d.decision == "unsure"
    assert d.reason_code == "NO_RULE_MATCHED"


def test_decision_parser_handles_fenced_json():
    text = "```json\n{\"decision\": \"include\", \"reason_code\": \"I1\", \"reason_text\": \"OK\", \"confidence\": 0.9}\n```"
    parsed = _parse_decision(text)
    assert parsed["decision"] == "include"
    assert parsed["reason_code"] == "I1"


def test_decision_parser_handles_bare_json():
    text = 'Sure. {"decision": "exclude", "reason_code": "E2", "reason_text": "lang", "confidence": 0.7}'
    parsed = _parse_decision(text)
    assert parsed["decision"] == "exclude"


def test_decision_parser_returns_unsure_on_garbage():
    parsed = _parse_decision("hello world")
    assert parsed["decision"] == "unsure"
    assert parsed["reason_code"] == "PARSE_ERROR"


class _FakeProvider(Provider):
    name = "fake"
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.last_system = None
        self.last_messages = None
        self.last_attachments = None

    def complete(self, system, messages, model, max_tokens=1024, temperature=0.0,
                 response_format="json", attachments=None):
        self.last_system = system
        self.last_messages = messages
        self.last_attachments = attachments
        return ProviderResponse(text=self.response_text, input_tokens=10, output_tokens=10)


def test_llm_screener_include():
    fake = _FakeProvider('{"decision":"include","reason_code":"I1","reason_text":"meets criteria","confidence":0.95}')
    s = LLMScreener(
        provider=fake, model="x",
        review_title="Test review",
        inclusion=[Criterion(id="I1", text="must be about X")],
        exclusion=[Criterion(id="E1", text="must not be Y")],
    )
    d = s.decide({"record_id": 1, "title": "A paper", "abstract": "About X."})
    assert d.decision == "include"
    assert d.reason_code == "I1"
    assert "Test review" in fake.last_system


def test_llm_screener_requires_fulltext():
    fake = _FakeProvider('{"decision":"include"}')
    s = LLMScreener(
        provider=fake, model="x", review_title="T",
        inclusion=[], exclusion=[], require_fulltext=True,
    )
    d = s.decide({"record_id": 1, "title": "x", "abstract": "y"}, fulltext=None)
    assert d.decision == "unsure"
    assert d.reason_code == "NO_FULLTEXT"
    # Should not have called the provider
    assert fake.last_system is None


def test_llm_screener_attaches_pdf(tmp_path):
    """In PDF-direct mode the screener reads the PDF and passes it as an attachment."""
    pdf = tmp_path / "deadbeef.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    fake = _FakeProvider('{"decision":"include","reason_code":"I1","reason_text":"ok","confidence":0.9}')
    s = LLMScreener(
        provider=fake, model="x", review_title="T",
        inclusion=[Criterion(id="I1", text="about X")], exclusion=[],
    )
    d = s.decide({"record_id": 1, "title": "A paper", "abstract": "About X."},
                 fulltext_pdf=pdf)
    assert d.decision == "include"
    # The PDF bytes were forwarded as an attachment, not inlined as text.
    assert fake.last_attachments is not None
    assert len(fake.last_attachments) == 1
    assert fake.last_attachments[0].filename == "deadbeef.pdf"
    assert fake.last_attachments[0].data == b"%PDF-1.4 fake bytes"
    # The prompt should reference the attached PDF, not contain markdown.
    user_text = fake.last_messages[0].content
    assert "attached as a PDF" in user_text
    assert "markdown" not in user_text.lower()


def test_llm_screener_pdf_satisfies_require_fulltext(tmp_path):
    """A PDF counts as full text for the require_fulltext gate."""
    pdf = tmp_path / "abc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fake = _FakeProvider('{"decision":"include","reason_code":"I1","reason_text":"ok"}')
    s = LLMScreener(
        provider=fake, model="x", review_title="T",
        inclusion=[], exclusion=[], require_fulltext=True,
    )
    d = s.decide({"record_id": 1, "title": "x", "abstract": "y"}, fulltext_pdf=pdf)
    assert d.decision == "include"
    assert fake.last_attachments is not None


def test_llm_screener_prefers_markdown_over_pdf(tmp_path):
    """If both are supplied, markdown wins and no PDF is attached."""
    pdf = tmp_path / "abc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    fake = _FakeProvider('{"decision":"include"}')
    s = LLMScreener(provider=fake, model="x", review_title="T", inclusion=[], exclusion=[])
    s.decide({"record_id": 1, "title": "x", "abstract": "y"},
             fulltext="# Extracted markdown", fulltext_pdf=pdf)
    assert fake.last_attachments is None
    assert "Extracted markdown" in fake.last_messages[0].content
