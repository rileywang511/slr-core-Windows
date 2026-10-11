from __future__ import annotations

from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


BASE_URL = "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi"


class BaseResolver(Resolver):
    """BASE (Bielefeld Academic Search Engine).

    Requires institutional registration for the IP/token. The free endpoint
    returns XML; we use it to find OA-available URLs by DOI.
    """

    name = "base"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        params = {"func": "PerformSearch", "query": f"dcdoi:{doi}", "format": "json", "hits": "1"}
        try:
            resp = self.http.get(BASE_URL, params=params)
        except Exception as exc:
            return ResolveResult(error=f"http error: {exc}")
        if resp.status_code >= 400:
            return ResolveResult(error=f"http {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return ResolveResult(error="non-JSON response (likely IP not whitelisted)")
        docs = ((data.get("response") or {}).get("docs")) or []
        if not docs:
            return ResolveResult(error="not found")
        doc = docs[0]
        url = doc.get("dclink") or doc.get("dcidentifier")
        if isinstance(url, list):
            url = url[0] if url else None
        if not url:
            return ResolveResult(error="no link in BASE doc", raw=doc)
        return ResolveResult(url=url, raw=doc)
