from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from slr.llm.base import Message, PdfAttachment, Provider
from slr.protocol import Criterion
from slr.screen.base import Decision, Screener


SYSTEM_TEMPLATE = """You are a careful systematic-review screener.

Review: {review_title}

Inclusion criteria (a record must satisfy ALL of these to be included):
{inclusion}

Exclusion criteria (a record must NOT match ANY of these):
{exclusion}

Decide whether the record below should be INCLUDED, EXCLUDED, or marked UNSURE.

Respond with a single JSON object — no prose around it — with this schema:
{{
  "decision": "include" | "exclude" | "unsure",
  "reason_code": "<criterion id like I1 or E2, or NO_FULLTEXT, or UNCLEAR>",
  "reason_text": "<one short sentence>",
  "confidence": <number between 0 and 1>
}}
"""


def _format_criteria(criteria: list[Criterion]) -> str:
    if not criteria:
        return "(none)"
    return "\n".join(f"  - {c.id}: {c.text}" for c in criteria)


class LLMScreener(Screener):
    def __init__(
        self,
        provider: Provider,
        model: str,
        review_title: str,
        inclusion: list[Criterion],
        exclusion: list[Criterion],
        require_fulltext: bool = False,
    ):
        self.provider = provider
        self.model = model
        self.review_title = review_title
        self.inclusion = inclusion
        self.exclusion = exclusion
        self.require_fulltext = require_fulltext
        self.name = f"{provider.name}:{model}"

    def _build_system(self) -> str:
        return SYSTEM_TEMPLATE.format(
            review_title=self.review_title,
            inclusion=_format_criteria(self.inclusion),
            exclusion=_format_criteria(self.exclusion),
        )

    def _build_user(
        self, record: dict[str, Any], fulltext: str | None, has_pdf: bool
    ) -> str:
        body = (
            f"Title: {record.get('title') or ''}\n"
            f"Year: {record.get('year') or ''}\n"
            f"Venue: {record.get('venue') or ''}\n"
            f"Abstract: {record.get('abstract') or ''}"
        )
        if fulltext:
            body += f"\n\n--- FULL TEXT (markdown) ---\n{fulltext}"
        elif has_pdf:
            body += "\n\n--- FULL TEXT ---\nThe full text is attached as a PDF document."
        return body

    def decide(
        self,
        record: dict[str, Any],
        fulltext: str | None = None,
        fulltext_pdf: Path | None = None,
    ) -> Decision:
        if self.require_fulltext and not fulltext and not fulltext_pdf:
            return Decision(
                record_id=record["record_id"],
                decision="unsure",
                reason_code="NO_FULLTEXT",
                reason_text="full text required but not retrieved",
            )
        attachments: list[PdfAttachment] | None = None
        if fulltext_pdf is not None and not fulltext:
            pdf_path = Path(fulltext_pdf)
            attachments = [PdfAttachment(filename=pdf_path.name, data=pdf_path.read_bytes())]
        system = self._build_system()
        user_msg = Message(
            role="user",
            content=self._build_user(record, fulltext, has_pdf=attachments is not None),
        )
        t0 = time.time()
        complete_kwargs: dict[str, Any] = {}
        if attachments:
            complete_kwargs["attachments"] = attachments
        resp = self.provider.complete(
            system=system,
            messages=[user_msg],
            model=self.model,
            max_tokens=512,
            temperature=0.0,
            response_format="json",
            **complete_kwargs,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        parsed = _parse_decision(resp.text)
        return Decision(
            record_id=record["record_id"],
            decision=parsed.get("decision", "unsure"),
            reason_code=parsed.get("reason_code"),
            reason_text=parsed.get("reason_text"),
            confidence=parsed.get("confidence"),
            raw_response=resp.text,
            model_id=self.model,
            prompt=f"SYSTEM:\n{system}\n\nUSER:\n{user_msg.content}",
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            latency_ms=elapsed_ms,
        )


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_decision(text: str) -> dict[str, Any]:
    """Pull JSON out of the model response; tolerate fenced code blocks."""
    m = _JSON_RE.search(text)
    if not m:
        return {"decision": "unsure", "reason_code": "PARSE_ERROR", "reason_text": text[:200]}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"decision": "unsure", "reason_code": "PARSE_ERROR", "reason_text": text[:200]}
