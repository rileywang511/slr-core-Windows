from __future__ import annotations

from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"


class SemanticScholarResolver(Resolver):
    name = "semantic_scholar"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        try:
            resp = self.http.get(
                f"{BASE_URL}/DOI:{doi}",
                params={"fields": "openAccessPdf,isOpenAccess"},
                headers=headers,
            )
        except Exception as exc:
            return ResolveResult(error=f"http error: {exc}")
        if resp.status_code == 404:
            return ResolveResult(error="not found")
        if resp.status_code >= 400:
            return ResolveResult(error=f"http {resp.status_code}")
        data = resp.json()
        oa = data.get("openAccessPdf") or {}
        url = oa.get("url")
        if not url:
            return ResolveResult(error="no openAccessPdf", raw=data)
        return ResolveResult(url=url, raw=data)
