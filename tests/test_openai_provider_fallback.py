"""Test OpenAIProvider's response_format fallback for LM Studio / llama.cpp."""

from __future__ import annotations

import pytest
import httpx

from slr.llm.base import Message, PdfAttachment
from slr.llm.openai import OpenAIProvider

openai = pytest.importorskip("openai")


def _bad_request(msg: str) -> openai.BadRequestError:
    """Construct a real BadRequestError — the SDK requires httpx.Response."""
    req = httpx.Request("POST", "https://example/v1/chat/completions")
    resp = httpx.Response(400, request=req)
    return openai.BadRequestError(msg, response=resp, body=None)


class _StubChatCompletion:
    """Mimics the shape of openai.types.chat.ChatCompletion well enough."""

    def __init__(self, text: str):
        class _Msg:
            content = text
        class _Choice:
            message = _Msg()
        self.choices = [_Choice()]
        self.usage = None

    def model_dump(self) -> dict:
        return {"_stub": True}


def _make_provider_with_fake_client(fake_create):
    prov = OpenAIProvider(api_key="not-needed", base_url="http://example/v1", compatible=True)
    prov._client.chat.completions.create = fake_create  # type: ignore[attr-defined]
    return prov


def test_falls_back_to_text_when_server_rejects_json_object():
    """LM Studio raises BadRequestError mentioning response_format; we retry without it."""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            # First attempt: server complains about response_format
            raise _bad_request(
                "Error code: 400 - {'error': \"'response_format.type' must be 'json_schema' or 'text'\"}"
            )
        return _StubChatCompletion('{"decision":"include","reason_code":"I1",'
                                   '"reason_text":"ok","confidence":0.9}')

    prov = _make_provider_with_fake_client(fake_create)
    resp = prov.complete(
        system="s", messages=[Message(role="user", content="hi")],
        model="gpt-oss-20b", response_format="json",
    )

    assert len(calls) == 2
    # First call asked for json_object, second omitted response_format
    assert calls[0].get("response_format") == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert "include" in resp.text


def test_remembers_fallback_across_subsequent_calls():
    """After one failure, we shouldn't keep retrying — go straight to text mode."""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request("400 response_format invalid")
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")],
                  model="m", response_format="json")
    # Second call should NOT include response_format at all
    prov.complete(system="s", messages=[Message(role="user", content="y")],
                  model="m", response_format="json")
    assert len(calls) == 3   # retry on first invocation (2 calls) + clean second invocation (1 call)
    assert "response_format" not in calls[2]


def test_non_response_format_400_is_raised():
    """A 400 about something else (e.g. bad model name) should still propagate."""
    def fake_create(**kwargs):
        raise _bad_request("400 - {'error': 'model not found'}")

    prov = _make_provider_with_fake_client(fake_create)
    with pytest.raises(openai.BadRequestError):
        prov.complete(system="s", messages=[Message(role="user", content="x")],
                      model="bad-model", response_format="json")


def test_retries_with_max_completion_tokens_when_model_requires_it():
    """Newer OpenAI reasoning models reject the legacy max_tokens name."""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request(
                "Unsupported parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead."
            )
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(
        system="s",
        messages=[Message(role="user", content="x")],
        model="gpt-5-mini",
    )

    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 1024
    assert "max_completion_tokens" not in calls[0]
    assert calls[1]["max_completion_tokens"] == 1024
    assert "max_tokens" not in calls[1]


def test_remembers_max_completion_tokens_requirement_per_model():
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request("max_tokens is unsupported; use max_completion_tokens")
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")], model="gpt-5")
    prov.complete(system="s", messages=[Message(role="user", content="y")], model="gpt-5")

    assert len(calls) == 3
    assert "max_tokens" not in calls[2]
    assert calls[2]["max_completion_tokens"] == 1024


def test_retries_without_temperature_when_model_requires_its_default():
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request(
                "Unsupported value: 'temperature' does not support 0.0 with this model. "
                "Only the default (1) value is supported."
            )
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")], model="gpt-5")

    assert len(calls) == 2
    assert calls[0]["temperature"] == 0.0
    assert "temperature" not in calls[1]


def test_remembers_default_temperature_requirement_per_model():
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request("temperature only supports the default value")
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")], model="gpt-5")
    prov.complete(system="s", messages=[Message(role="user", content="y")], model="gpt-5")

    assert len(calls) == 3
    assert "temperature" not in calls[2]


def test_combines_model_parameter_fallbacks_in_one_request():
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _bad_request("max_tokens is unsupported; use max_completion_tokens")
        if len(calls) == 2:
            raise _bad_request("temperature only supports the default value")
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")], model="gpt-5")

    assert len(calls) == 3
    assert "max_completion_tokens" in calls[2]
    assert "temperature" not in calls[2]


def test_response_format_text_doesnt_send_json_object():
    """When the caller explicitly asks for text mode, we never send the param."""
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return _StubChatCompletion('plain text reply')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="x")],
                  model="m", response_format="text")
    assert "response_format" not in calls[0]


def test_pdf_attachment_becomes_file_content_part():
    """A PdfAttachment is rendered as an inline base64 `file` part on the user turn."""
    import base64

    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(
        system="s",
        messages=[Message(role="user", content="screen this")],
        model="gpt-4o",
        attachments=[PdfAttachment(filename="paper.pdf", data=b"%PDF-1.4 x")],
    )
    # messages[0] is the system turn; messages[1] is the user turn with parts.
    user_msg = calls[0]["messages"][1]
    assert user_msg["role"] == "user"
    parts = user_msg["content"]
    assert parts[0] == {"type": "text", "text": "screen this"}
    file_part = parts[1]
    assert file_part["type"] == "file"
    assert file_part["file"]["filename"] == "paper.pdf"
    expected = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 x").decode()
    assert file_part["file"]["file_data"] == expected


def test_no_attachment_keeps_plain_string_content():
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return _StubChatCompletion('{"decision":"include"}')

    prov = _make_provider_with_fake_client(fake_create)
    prov.complete(system="s", messages=[Message(role="user", content="hi")], model="m")
    assert calls[0]["messages"][1] == {"role": "user", "content": "hi"}
