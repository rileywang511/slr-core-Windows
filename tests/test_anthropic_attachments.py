"""AnthropicProvider PDF-attachment rendering (no network — tests the builder)."""

from __future__ import annotations

import base64

from slr.llm.anthropic import _build_messages
from slr.llm.base import Message, PdfAttachment


def test_no_attachments_keeps_plain_string():
    msgs = _build_messages([Message(role="user", content="hello")], None)
    assert msgs == [{"role": "user", "content": "hello"}]


def test_pdf_becomes_base64_document_block_on_last_user_turn():
    data = b"%PDF-1.4 contents"
    msgs = _build_messages(
        [Message(role="user", content="screen this")],
        [PdfAttachment(filename="paper.pdf", data=data)],
    )
    assert len(msgs) == 1
    content = msgs[0]["content"]
    assert content[0] == {"type": "text", "text": "screen this"}
    doc = content[1]
    assert doc["type"] == "document"
    assert doc["source"]["type"] == "base64"
    assert doc["source"]["media_type"] == "application/pdf"
    assert doc["source"]["data"] == base64.standard_b64encode(data).decode("ascii")


def test_attachment_targets_only_the_last_user_turn():
    msgs = _build_messages(
        [
            Message(role="user", content="first"),
            Message(role="assistant", content="ok"),
            Message(role="user", content="second"),
        ],
        [PdfAttachment(filename="p.pdf", data=b"%PDF")],
    )
    # earlier user turn + assistant turn stay plain strings
    assert msgs[0] == {"role": "user", "content": "first"}
    assert msgs[1] == {"role": "assistant", "content": "ok"}
    # only the final user turn carries the document block
    assert isinstance(msgs[2]["content"], list)
    assert any(p.get("type") == "document" for p in msgs[2]["content"])
