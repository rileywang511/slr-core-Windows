"""Methodological declaration for a systematic literature review.

protocol.yaml is the canonical, shareable description of the review: query,
sources, criteria, screening *type* per stage, dedup parameters. It's
designed to travel alongside review outputs for PRISMA reproducibility.

System / environment concerns (API providers, contact emails, fulltext
acquisition settings, marker-pdf config) live in `config.yaml` — see
`slr.config`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictProtocolModel(BaseModel):
    """Reject inert methodology keys instead of silently accepting typos."""

    model_config = ConfigDict(extra="forbid")


class Criterion(StrictProtocolModel):
    id: str
    text: str


class DateRange(StrictProtocolModel):
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "DateRange":
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("date_range.from must not be after date_range.to")
        return self


class ScreenStage(StrictProtocolModel):
    """Methodology declaration for one stage of screening.

    System plumbing (provider/model/base_url) lives in config.yaml, not here.
    Fields here describe WHAT the screener does — not HOW it's reached.
    """

    type: Literal["llm", "rules", "manual"] = "manual"
    fulltext: Literal["required", "optional", "ignored"] = "ignored"
    rules: list[dict[str, Any]] = Field(default_factory=list)


class ScreenMethodology(StrictProtocolModel):
    title_abstract: ScreenStage = Field(default_factory=ScreenStage)
    full_text: ScreenStage = Field(default_factory=ScreenStage)


class DedupConfig(StrictProtocolModel):
    fuzzy_title_threshold: int = Field(default=90, ge=0, le=100)


class Protocol(StrictProtocolModel):
    review_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    created: date | None = None
    query: str
    sources: list[str]
    date_range: DateRange = Field(default_factory=DateRange)
    inclusion_criteria: list[Criterion] = Field(default_factory=list)
    exclusion_criteria: list[Criterion] = Field(default_factory=list)
    screen: ScreenMethodology = Field(default_factory=ScreenMethodology)
    dedup: DedupConfig = Field(default_factory=DedupConfig)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "Protocol":
        duplicate_criteria = [
            criterion_id
            for criterion_id in {criterion.id for criterion in self.inclusion_criteria + self.exclusion_criteria}
            if sum(
                criterion.id == criterion_id
                for criterion in self.inclusion_criteria + self.exclusion_criteria
            ) > 1
        ]
        if duplicate_criteria:
            raise ValueError(
                "criterion IDs must be unique across inclusion/exclusion criteria: "
                + ", ".join(sorted(duplicate_criteria))
            )
        if not self.sources:
            raise ValueError("sources must contain at least one source")
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("sources must not contain duplicates")
        return self

    def hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def load_protocol(path: str | Path) -> Protocol:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"protocol not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8-sig"))
    return Protocol.model_validate(data)


EXAMPLE_PROTOCOL = """\
# protocol.yaml — methodological declaration of the review.
# Share this alongside your review outputs for PRISMA reproducibility.
# System settings (API providers, contact email, paths) live in config.yaml.

review_id: example-2026
title: "Example review — narrow CRISPR off-target topic"
authors: ["Your Name"]
created: 2026-05-15

# WoS-style boolean query (TS/TI/AB/AU/PY/SO/DT/LA + AND/OR/NOT/NEAR/parens/wildcards).
query: |
  TS=("CRISPR" AND "off-target")
  AND PY=2023-2026

# Which databases to query. See `slr` README for the full list.
sources: [openalex, europepmc]

date_range:
  from: 2023-01-01
  to: 2026-05-15

inclusion_criteria:
  - { id: I1, text: "Reports off-target effects of CRISPR-Cas systems" }
  - { id: I2, text: "Empirical experimental study" }
exclusion_criteria:
  - { id: E1, text: "Review, editorial, or commentary" }
  - { id: E2, text: "Non-English full text" }

# Per-stage screening METHODOLOGY. The LLM provider / model / base_url for
# `type: llm` stages live in config.yaml. The rules patterns below stay here
# because they're part of the methodology (auditable, reproducible).
screen:
  title_abstract:
    type: llm                         # llm | rules | manual
    # For type=rules, declare patterns here:
    # rules:
    #   - { action: exclude, field: title, pattern: '\\\\breview\\\\b', reason_code: E1, reason_text: "Review article" }
  full_text:
    type: manual
    fulltext: ignored                 # required | optional | ignored

dedup:
  fuzzy_title_threshold: 90
"""
