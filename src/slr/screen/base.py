from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass
class Decision:
    record_id: int
    decision: Literal["include", "exclude", "unsure"]
    reason_code: str | None = None
    reason_text: str | None = None
    confidence: float | None = None
    raw_response: str | None = None  # for LLM screeners — full model output
    model_id: str | None = None
    prompt: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


class Screener(ABC):
    name: str

    @abstractmethod
    def decide(
        self,
        record: dict[str, Any],
        fulltext: str | None = None,
        fulltext_pdf: Path | None = None,
    ) -> Decision: ...

    def screener_id(self) -> str:
        return self.name
