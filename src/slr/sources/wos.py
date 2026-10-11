"""Web of Science via Clarivate WoS Starter / Expanded API."""

from __future__ import annotations

import os
from typing import Any

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.wos import WosEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult


BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"


class WosSource(Source):
    """Web of Science Starter API. Requires WOS_API_KEY."""

    name = "wos"

    def __init__(self, contact_email: str | None = None, http_client: httpx.Client | None = None,
                 api_key: str | None = None, http_config: HttpConfig | None = None):
        super().__init__(contact_email=contact_email, http_client=http_client, http_config=http_config)
        self.api_key = api_key or os.environ.get("WOS_API_KEY")
        if not self.api_key:
            raise RuntimeError("WOS_API_KEY env var not set; required for WoS source")
        if self.http is None:
            self.http = httpx.Client(
                timeout=30.0,
                headers={"X-ApiKey": self.api_key, "Accept": "application/json"},
            )

    def search(self, expr: Expr, date_from: str | None = None, date_to: str | None = None,
               limit: int | None = None, progress=None) -> SourceResult:
        emitter = WosEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        params: dict[str, Any] = {
            "db": "WOS",
            "q": emitted.query_string,
            "limit": "50",
            "page": "1",
        }
        raw_records: list[dict[str, Any]] = []
        page = 1
        n_total: int | None = None
        on_retry = (lambda ev: progress({**ev, "source": self.name})) if progress else None
        while True:
            params["page"] = str(page)
            resp = self.request("GET", BASE_URL, params=params, on_event=on_retry)
            payload = resp.json()
            hits = payload.get("hits") or []
            raw_records.extend(hits)
            meta = payload.get("metadata") or {}
            if n_total is None:
                try:
                    n_total = int(meta.get("total") or 0)
                except (TypeError, ValueError):
                    n_total = None
            if progress:
                progress({"source": self.name, "page": page,
                          "n_so_far": len(raw_records), "n_total": n_total, "limit": limit})
            if limit is not None and len(raw_records) >= limit:
                raw_records = raw_records[:limit]
                break
            total = int(meta.get("total") or 0)
            if page * 50 >= total or not hits:
                break
            page += 1

        return SourceResult(
            raw_records=raw_records,
            query_translated=emitted.query_string,
            warnings=emitted.warnings,
        )

    def normalize(self, raw: dict[str, Any]) -> Record:
        title = raw.get("title", {}).get("value") if isinstance(raw.get("title"), dict) else raw.get("title") or ""
        ids = raw.get("identifiers") or {}
        doi = ids.get("doi") or ids.get("DOI")
        authors_data = raw.get("names", {}).get("authors") or raw.get("authors") or []
        authors = []
        for a in authors_data:
            if isinstance(a, dict):
                authors.append(Author(family=a.get("lastName") or a.get("displayName"),
                                      given=a.get("firstName")))
            else:
                authors.append(Author(family=str(a)))
        year = None
        src = raw.get("source") or {}
        py = src.get("publishYear") or raw.get("publicationYear")
        if py:
            try:
                year = int(py)
            except (TypeError, ValueError):
                year = None
        return Record(
            source="wos",
            source_id=raw.get("uid") or raw.get("UT") or "",
            doi=doi,
            title=title,
            abstract=raw.get("abstract"),
            authors=authors,
            year=year,
            venue=(src.get("sourceTitle") if isinstance(src, dict) else None) or raw.get("source"),
            type=raw.get("documentTypes", [None])[0] if raw.get("documentTypes") else None,
            url=None,
            raw=raw,
        )
