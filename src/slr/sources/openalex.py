from __future__ import annotations

from typing import Any

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.openalex import OpenAlexEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult

BASE_URL = "https://api.openalex.org/works"
DEFAULT_PER_PAGE = 100


class OpenAlexSource(Source):
    name = "openalex"

    def __init__(self, contact_email: str | None = None, http_client: httpx.Client | None = None,
                 http_config: HttpConfig | None = None):
        super().__init__(contact_email=contact_email, http_client=http_client, http_config=http_config)
        if self.http is None:
            self.http = httpx.Client(timeout=30.0)

    def search(
        self,
        expr: Expr,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int | None = None,
        progress=None,
    ) -> SourceResult:
        emitter = OpenAlexEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        params: dict[str, Any] = {
            "filter": emitted.filter_string,
            "per-page": DEFAULT_PER_PAGE,
            "cursor": "*",
        }
        if self.contact_email:
            params["mailto"] = self.contact_email

        raw_records: list[dict[str, Any]] = []
        cursor: str | None = "*"
        page = 0
        n_total: int | None = None
        on_retry = (lambda ev: progress({**ev, "source": self.name})) if progress else None
        while cursor:
            params["cursor"] = cursor
            resp = self.request("GET", BASE_URL, params=params, on_event=on_retry)
            payload = resp.json()
            results = payload.get("results", [])
            raw_records.extend(results)
            page += 1
            if n_total is None:
                n_total = (payload.get("meta") or {}).get("count")
            if progress:
                progress({
                    "source": self.name,
                    "page": page,
                    "n_so_far": len(raw_records),
                    "n_total": n_total,
                    "limit": limit,
                })
            if limit is not None and len(raw_records) >= limit:
                raw_records = raw_records[:limit]
                break
            cursor = payload.get("meta", {}).get("next_cursor")
            if not cursor or not results:
                break

        return SourceResult(
            raw_records=raw_records,
            query_translated=f"filter={emitted.filter_string}",
            warnings=emitted.warnings,
        )

    def normalize(self, raw: dict[str, Any]) -> Record:
        authorships = raw.get("authorships") or []
        authors = []
        for a in authorships:
            au = a.get("author") or {}
            authors.append(
                Author(
                    family=_last_name(au.get("display_name")),
                    given=_first_names(au.get("display_name")),
                    orcid=au.get("orcid"),
                    affiliation=(a.get("institutions") or [{}])[0].get("display_name") if a.get("institutions") else None,
                )
            )
        loc = raw.get("primary_location") or {}
        venue = (loc.get("source") or {}).get("display_name")
        oa = raw.get("open_access") or {}
        ids = raw.get("ids") or {}
        return Record(
            source="openalex",
            source_id=raw.get("id", "").split("/")[-1] or raw.get("doi", ""),
            doi=raw.get("doi"),
            title=raw.get("display_name") or raw.get("title") or "",
            abstract=_reconstruct_abstract(raw.get("abstract_inverted_index")),
            authors=authors,
            year=raw.get("publication_year"),
            venue=venue,
            type=raw.get("type"),
            url=oa.get("oa_url") or ids.get("openalex"),
            pmid=_extract_pmid(ids.get("pmid")),
            pmcid=_extract_pmcid(ids.get("pmcid")),
            raw=raw,
        )


def _reconstruct_abstract(inv: dict[str, list[int]] | None) -> str | None:
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _last_name(display_name: str | None) -> str | None:
    if not display_name:
        return None
    parts = display_name.strip().split()
    return parts[-1] if parts else None


def _first_names(display_name: str | None) -> str | None:
    if not display_name:
        return None
    parts = display_name.strip().split()
    return " ".join(parts[:-1]) if len(parts) > 1 else None


def _extract_pmid(s: str | None) -> str | None:
    if not s:
        return None
    return s.rstrip("/").split("/")[-1]


def _extract_pmcid(s: str | None) -> str | None:
    if not s:
        return None
    return s.rstrip("/").split("/")[-1]
