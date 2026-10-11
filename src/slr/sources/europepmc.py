from __future__ import annotations

from typing import Any

import httpx

from slr.config import HttpConfig
from slr.query.ast import Expr
from slr.query.emitters.europepmc import EuropePMCEmitter
from slr.record import Author, Record
from slr.sources.base import Source, SourceResult

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
DEFAULT_PAGE_SIZE = 100


class EuropePMCSource(Source):
    name = "europepmc"

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
        emitter = EuropePMCEmitter()
        emitted = emitter.emit(expr, date_from=date_from, date_to=date_to)
        params: dict[str, Any] = {
            "query": emitted.query_string,
            "format": "json",
            "pageSize": DEFAULT_PAGE_SIZE,
            "resultType": "core",
            "cursorMark": "*",
        }
        raw_records: list[dict[str, Any]] = []
        cursor: str | None = "*"
        seen_cursors: set[str] = set()
        page = 0
        n_total: int | None = None
        on_retry = (lambda ev: progress({**ev, "source": self.name})) if progress else None
        while cursor and cursor not in seen_cursors:
            seen_cursors.add(cursor)
            params["cursorMark"] = cursor
            resp = self.request("GET", BASE_URL, params=params, on_event=on_retry)
            payload = resp.json()
            results = (payload.get("resultList") or {}).get("result", [])
            raw_records.extend(results)
            page += 1
            if n_total is None:
                hit = payload.get("hitCount")
                if isinstance(hit, int):
                    n_total = hit
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
            cursor = payload.get("nextCursorMark")
            if not results:
                break

        return SourceResult(
            raw_records=raw_records,
            query_translated=f"query={emitted.query_string}",
            warnings=emitted.warnings,
        )

    def normalize(self, raw: dict[str, Any]) -> Record:
        author_list = (raw.get("authorList") or {}).get("author") or []
        authors = [
            Author(
                family=a.get("lastName"),
                given=a.get("firstName") or a.get("initials"),
                orcid=(a.get("authorId") or {}).get("value") if isinstance(a.get("authorId"), dict) else None,
                affiliation=a.get("affiliation"),
            )
            for a in author_list
        ]
        year = None
        pubyear = raw.get("pubYear")
        if pubyear:
            try:
                year = int(pubyear)
            except (TypeError, ValueError):
                year = None
        pmid = raw.get("pmid")
        pmcid = raw.get("pmcid")
        doi = raw.get("doi")
        url = None
        if pmcid:
            url = f"https://europepmc.org/article/PMC/{pmcid}"
        elif pmid:
            url = f"https://europepmc.org/article/MED/{pmid}"
        return Record(
            source="europepmc",
            source_id=raw.get("id") or pmid or pmcid or doi or "",
            doi=doi,
            title=raw.get("title") or "",
            abstract=raw.get("abstractText"),
            authors=authors,
            year=year,
            venue=raw.get("journalTitle") or raw.get("bookOrReportDetails", {}).get("publisher"),
            type=raw.get("pubType"),
            url=url,
            pmid=pmid,
            pmcid=pmcid,
            raw=raw,
        )
