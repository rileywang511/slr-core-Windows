from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class PdfAttachment:
    """A PDF to hand to the model directly (no markdown conversion).

    Providers that can read PDFs natively (Anthropic, OpenAI) attach this to
    the user turn; `data` is the raw PDF bytes.
    """

    filename: str
    data: bytes


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderSpec:
    """Describes how to construct a Provider — populated from protocol.yaml."""

    provider: Literal["anthropic", "openai", "openai-compatible"]
    model: str
    base_url: str | None = None
    api_key_env: str | None = None  # which env var holds the key


class Provider(ABC):
    name: str

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        response_format: Literal["text", "json"] = "json",
        attachments: list[PdfAttachment] | None = None,
    ) -> ProviderResponse: ...
