from __future__ import annotations

import base64
import os
import sys
from typing import Literal

from slr.llm.base import Message, PdfAttachment, Provider, ProviderResponse


class OpenAIProvider(Provider):
    """OpenAI provider. Reused for OpenAI-compatible endpoints by passing base_url.

    Some OpenAI-compatible servers (LM Studio, older llama.cpp builds) reject
    `response_format: {"type": "json_object"}` with HTTP 400. This provider
    auto-detects that on the first call and falls back to plain-text mode for
    the rest of the session — the screener's regex JSON extractor still pulls
    decisions out fine. Newer OpenAI reasoning models may reject the legacy
    ``max_tokens`` name in favour of ``max_completion_tokens`` or disallow a
    non-default ``temperature``; both constraints are detected and remembered
    per model.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        compatible: bool = False,
    ):
        try:
            import openai  # type: ignore
        except ImportError as e:
            raise ImportError(
                "openai SDK not installed; install with `pip install 'slr-agent[llm]'`"
            ) from e
        self.base_url = base_url
        self._openai = openai
        # Local servers (Ollama etc.) often accept any key; allow a sentinel.
        env_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not env_key:
            if compatible:
                env_key = "not-needed"
            else:
                raise RuntimeError("OPENAI_API_KEY not set")
        self.api_key = env_key
        kwargs: dict = {"api_key": env_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        if compatible:
            self.name = "openai-compatible"
        # Sticky once we discover the server rejects json_object.
        self._json_object_unsupported = False
        self._models_requiring_max_completion_tokens: set[str] = set()
        self._models_requiring_default_temperature: set[str] = set()

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
        api_messages: list[dict] = [{"role": "system", "content": system}]
        api_messages.extend(_render_messages(messages, attachments))

        params: dict = {
            "model": model,
            "messages": api_messages,
        }
        if model not in self._models_requiring_default_temperature:
            params["temperature"] = temperature
        token_parameter = (
            "max_completion_tokens"
            if model in self._models_requiring_max_completion_tokens
            else "max_tokens"
        )
        params[token_parameter] = max_tokens
        want_json = response_format == "json" and not self._json_object_unsupported
        if want_json:
            params["response_format"] = {"type": "json_object"}

        while True:
            try:
                resp = self._client.chat.completions.create(**params)
                break
            except self._openai.BadRequestError as exc:
                if "max_tokens" in params and _is_max_completion_tokens_error(exc):
                    # GPT-5 / newer reasoning models use the renamed parameter.
                    self._models_requiring_max_completion_tokens.add(model)
                    params["max_completion_tokens"] = params.pop("max_tokens")
                    continue
                if "temperature" in params and _is_default_temperature_only_error(exc):
                    # Some reasoning models only accept their API default (1).
                    self._models_requiring_default_temperature.add(model)
                    params.pop("temperature")
                    continue
                if want_json and "response_format" in params and _is_response_format_error(exc):
                    # Server doesn't accept json_object — retry once without it.
                    self._json_object_unsupported = True
                    sys.stderr.write(
                        f"  [{self.name}] note: server rejected response_format=json_object; "
                        f"falling back to plain text for this session\n"
                    )
                    sys.stderr.flush()
                    params.pop("response_format")
                    continue
                raise

        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            raw=resp.model_dump() if hasattr(resp, "model_dump") else {},
        )


def _render_messages(
    messages: list[Message], attachments: list[PdfAttachment] | None
) -> list[dict]:
    """Render messages, attaching any PDFs as `file` parts on the last user turn.

    Inline PDF files are supported by OpenAI's own multimodal models; most
    OpenAI-compatible local servers do not accept them and will 400.
    """
    if not attachments:
        return [{"role": m.role, "content": m.content} for m in messages]

    last_user = max(
        (i for i, m in enumerate(messages) if m.role == "user"), default=None
    )
    out: list[dict] = []
    for i, m in enumerate(messages):
        if i == last_user:
            content: list[dict] = [{"type": "text", "text": m.content}]
            for att in attachments:
                b64 = base64.b64encode(att.data).decode("ascii")
                content.append({
                    "type": "file",
                    "file": {
                        "filename": att.filename,
                        "file_data": f"data:application/pdf;base64,{b64}",
                    },
                })
            out.append({"role": m.role, "content": content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _is_response_format_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "response_format" in msg


def _is_max_completion_tokens_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "max_tokens" in msg and "max_completion_tokens" in msg


def _is_default_temperature_only_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "temperature" in msg and "default" in msg and "only" in msg
