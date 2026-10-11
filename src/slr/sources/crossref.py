from __future__ import annotations

from typing import Any

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.crossref import CrossrefEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult


BASE_URL = "https://api.crossref.org/works"


class CrossrefSource(Source):
    name = "crossref"

    def __init__(self, contact_email: str | None = None, http_client: httpx.Client | None = None,
                 http_config: HttpConfig | None = None):
        super().__init__(contact_email=contact_email, http_client=http_client, http_config=http_config)
        if self.http is None:
            headers = {"User-Agent": f"slr-agent (mailto:{contact_email})"} if contact_email else {}
            self.http = httpx.Client(timeout=30.0, headers=headers)

    def search(self, expr: Expr, date_from: str | None = None, date_to: str | None = None,
               limit: int | None = None, progress=None) -> SourceResult:
        emitter = CrossrefEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        params: dict[str, Any] = {
            "query": emitted.query,
            "rows": "100",
            "cursor": "*",
        }
        if emitted.filters:
            params["filter"] = ",".join(f"{k}:{v}" for k, v in emitted.filters.items())
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
            message = payload.get("message") or {}
            items = message.get("items") or []
            raw_records.extend(items)
            page += 1
            if n_total is None:
                n_total = message.get("total-results")
            if progress:
                progress({"source": self.name, "page": page,
                          "n_so_far": len(raw_records), "n_total": n_total, "limit": limit})
            if limit is not None and len(raw_records) >= limit:
                raw_records = raw_records[:limit]
                break
            new_cursor = message.get("next-cursor")
            if not new_cursor or new_cursor == cursor or not items:
                break
            cursor = new_cursor

        return SourceResult(
            raw_records=raw_records,
            query_translated=f"query={emitted.query}; filter={emitted.filters}",
            warnings=emitted.warnings,
        )

    def normalize(self, raw: dict[str, Any]) -> Record:
        authors = []
        for a in raw.get("author") or []:
            authors.append(
                Author(
                    family=a.get("family"),
                    given=a.get("given"),
                    orcid=a.get("ORCID"),
                    affiliation=(a.get("affiliation") or [{}])[0].get("name") if a.get("affiliation") else None,
                )
            )
        year = None
        issued = raw.get("issued") or {}
        date_parts = issued.get("date-parts") or [[None]]
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = int(date_parts[0][0])
        titles = raw.get("title") or [""]
        venue_list = raw.get("container-title") or [""]
        return Record(
            source="crossref",
            source_id=raw.get("DOI", ""),
            doi=raw.get("DOI"),
            title=titles[0] if titles else "",
            abstract=raw.get("abstract"),
            authors=authors,
            year=year,
            venue=venue_list[0] if venue_list else None,
            type=raw.get("type"),
            url=raw.get("URL"),
            raw=raw,
        )
