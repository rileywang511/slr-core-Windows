from __future__ import annotations

import base64
import os
from typing import Literal

from slr.llm.base import Message, PdfAttachment, Provider, ProviderResponse


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None):
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed; install with `pip install 'slr-agent[llm]'`"
            ) from e
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def complete(
        self,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        response_format: Literal["text", "json"] = "json",
        attachments: list[PdfAttachment] | None = None,
    ) -> ProviderResponse:
        # Cache the system block so batch screening reuses the cached prefix.
        system_block = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        api_messages = _build_messages(messages, attachments)

        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_block,
            messages=api_messages,
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )


def _build_messages(messages: list[Message], attachments: list[PdfAttachment] | None) -> list[dict]:
    """Render messages for the API, attaching any PDFs to the last user turn."""
    if not attachments:
        return [{"role": m.role, "content": m.content} for m in messages]

    last_user = max(
        (i for i, m in enumerate(messages) if m.role == "user"), default=None
    )
    api_messages: list[dict] = []
    for i, m in enumerate(messages):
        if i == last_user:
            content: list[dict] = [{"type": "text", "text": m.content}]
            for att in attachments:
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(att.data).decode("ascii"),
                    },
                })
            api_messages.append({"role": m.role, "content": content})
        else:
            api_messages.append({"role": m.role, "content": m.content})
    return api_messages
