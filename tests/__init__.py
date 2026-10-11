"""Test-only helpers for creating review-directory artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from slr import frames
from slr.record import Record


class ReviewFiles:
    """Small test convenience wrapper around the public file functions."""

    def __init__(self, review_dir: str | Path):
        self.review_dir = Path(review_dir)

    def init(self) -> None:
        frames.init(self.review_dir)

    def insert_source_run(self, *args: Any, **kwargs: Any) -> int:
        return frames.append_source_run(self.review_dir, *args, **kwargs)

    def insert_records(self, run_id: int, records: Iterable[Record]) -> int:
        return frames.append_provenance(self.review_dir, run_id, records)

    def all_records(self) -> list[dict[str, Any]]:
        return frames.read_provenance(self.review_dir)

    def record_count(self) -> int:
        return frames.record_count(self.review_dir) or len(frames.read_provenance(self.review_dir))

    def write_final_records(self, groups: list[dict[str, Any]]) -> None:
        frames.write_deduplicated_records(self.review_dir, groups)

    def final_records(self) -> list[dict[str, Any]]:
        return frames.final_records(self.review_dir)

    def set_stage(self, stage: str, input_hash: str, output_hash: str) -> None:
        frames.set_stage(self.review_dir, stage, input_hash, output_hash)

    def latest_decisions(self, stage: str) -> list[dict[str, Any]]:
        return frames.latest_decisions(self.review_dir, stage)

    def decision_counts(self, stage: str) -> dict[str, int]:
        return frames.decision_counts(self.review_dir, stage)

    def record_ids_with_decisions(self, stage: str) -> set[int]:
        return frames.record_ids_with_decisions(self.review_dir, stage)

    def write_screening_decisions(self, stage: str, decisions: list[dict[str, Any]]) -> None:
        for decision in decisions:
            self.append_screening_decision(stage, decision)

    def append_screening_decision(self, stage: str, decision: dict[str, Any]) -> None:
        frames.append_decision(self.review_dir, stage, decision)

    def insert_fulltext_attempt(self, attempt: dict[str, Any]) -> None:
        frames.append_fulltext_attempt(self.review_dir, attempt)

    def insert_conversion(self, conversion: dict[str, Any]) -> None:
        frames.append_conversion(self.review_dir, conversion)

    def record_ids_with_conversion_error(self) -> set[int]:
        return frames.record_ids_with_conversion_error(self.review_dir)
