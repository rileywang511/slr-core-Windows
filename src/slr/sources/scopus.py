from __future__ import annotations

import os
from typing import Any

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.scopus import ScopusEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult


BASE_URL = "https://api.elsevier.com/content/search/scopus"


class ScopusSource(Source):
    """Scopus search via Elsevier API. Requires SCOPUS_API_KEY."""

    name = "scopus"

    def __init__(self, contact_email: str | None = None, http_client: httpx.Client | None = None,
                 api_key: str | None = None, http_config: HttpConfig | None = None):
        super().__init__(contact_email=contact_email, http_client=http_client, http_config=http_config)
        self.api_key = api_key or os.environ.get("SCOPUS_API_KEY")
        if not self.api_key:
            raise RuntimeError("SCOPUS_API_KEY env var not set; required for Scopus source")
        if self.http is None:
            self.http = httpx.Client(
                timeout=30.0,
                headers={
                    "X-ELS-APIKey": self.api_key,
                    "Accept": "application/json",
                },
            )

    def search(self, expr: Expr, date_from: str | None = None, date_to: str | None = None,
               limit: int | None = None, progress=None) -> SourceResult:
        emitter = ScopusEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        params: dict[str, Any] = {
            "query": emitted.query_string,
            "count": "25",
            "start": "0",
        }
        raw_records: list[dict[str, Any]] = []
        start = 0
        page = 0
        n_total: int | None = None
        on_retry = (lambda ev: progress({**ev, "source": self.name})) if progress else None
        while True:
            params["start"] = str(start)
            resp = self.request("GET", BASE_URL, params=params, on_event=on_retry)
            payload = resp.json()
            search_results = payload.get("search-results") or {}
            entries = search_results.get("entry") or []
            raw_records.extend(entries)
            page += 1
            if n_total is None:
                try:
                    n_total = int(search_results.get("opensearch:totalResults") or 0)
                except (TypeError, ValueError):
                    n_total = None
            if progress:
                progress({"source": self.name, "page": page,
                          "n_so_far": len(raw_records), "n_total": n_total, "limit": limit})
            if limit is not None and len(raw_records) >= limit:
                raw_records = raw_records[:limit]
                break
            total = int(search_results.get("opensearch:totalResults") or 0)
            start += len(entries)
            if start >= total or not entries:
                break

        return SourceResult(
            raw_records=raw_records,
            query_translated=emitted.query_string,
            warnings=emitted.warnings,
        )

    def normalize(self, raw: dict[str, Any]) -> Record:
        authors = []
        if raw.get("author"):
            for a in raw["author"]:
                given = a.get("given-name")
                surname = a.get("surname")
                authors.append(Author(family=surname, given=given))
        else:
            creator = raw.get("dc:creator")
            if creator:
                authors.append(Author(family=creator))
        year = None
        cover_date = raw.get("prism:coverDate")
        if cover_date:
            try:
                year = int(cover_date.split("-")[0])
            except ValueError:
                year = None
        return Record(
            source="scopus",
            source_id=raw.get("dc:identifier") or raw.get("eid") or "",
            doi=raw.get("prism:doi"),
            title=raw.get("dc:title") or "",
            abstract=raw.get("dc:description"),
            authors=authors,
            year=year,
            venue=raw.get("prism:publicationName"),
            type=raw.get("subtypeDescription"),
            url=next(
                (link["@href"] for link in (raw.get("link") or []) if link.get("@ref") == "scopus"),
                None,
            ),
            raw=raw,
        )
