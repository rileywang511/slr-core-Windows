from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:", "DOI:")


def normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    for p in DOI_PREFIXES:
        if s.lower().startswith(p.lower()):
            s = s[len(p):]
            break
    s = s.strip().lower()
    return s or None


class Author(BaseModel):
    family: str | None = None
    given: str | None = None
    orcid: str | None = None
    affiliation: str | None = None

    def display(self) -> str:
        parts = [p for p in (self.given, self.family) if p]
        return " ".join(parts) if parts else (self.orcid or "")


class Record(BaseModel):
    source: str
    source_id: str
    doi: str | None = None
    title: str
    abstract: str | None = None
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    type: str | None = None
    url: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doi", mode="before")
    @classmethod
    def _normalize_doi(cls, v: Any) -> str | None:
        if v is None:
            return None
        return normalize_doi(str(v))

    def first_author_lastname(self) -> str | None:
        for a in self.authors:
            if a.family:
                return a.family.lower().strip()
        return None
