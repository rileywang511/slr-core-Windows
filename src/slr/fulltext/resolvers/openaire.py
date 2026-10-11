from __future__ import annotations

from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


BASE_URL = "https://api.openaire.eu/search/publications"


class OpenAIREResolver(Resolver):
    """OpenAIRE — EU OA aggregator. Returns JSON when format=json."""

    name = "openaire"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        try:
            resp = self.http.get(BASE_URL, params={"doi": doi, "format": "json", "size": 1})
        except Exception as exc:
            return ResolveResult(error=f"http error: {exc}")
        if resp.status_code >= 400:
            return ResolveResult(error=f"http {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            return ResolveResult(error="non-JSON response")
        # OpenAIRE response structure: response.results.result[].metadata."oaf:entity"."oaf:result".children.instance[].webresource[].url
        results = (((data.get("response") or {}).get("results") or {}).get("result")) or []
        if not isinstance(results, list):
            results = [results]
        for r in results:
            url = _find_pdf_url(r)
            if url:
                return ResolveResult(url=url, raw=r)
        return ResolveResult(error="no OA url in OpenAIRE record")


def _find_pdf_url(obj: Any) -> str | None:
    """Walk the nested OpenAIRE JSON looking for a downloadable URL."""
    if isinstance(obj, dict):
        # Common keys
        for key in ("downloadUrl", "url", "webresource"):
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
            if isinstance(val, dict):
                inner = _find_pdf_url(val)
                if inner:
                    return inner
            if isinstance(val, list):
                for item in val:
                    inner = _find_pdf_url(item)
                    if inner:
                        return inner
        for v in obj.values():
            inner = _find_pdf_url(v)
            if inner:
                return inner
    elif isinstance(obj, list):
        for item in obj:
            inner = _find_pdf_url(item)
            if inner:
                return inner
    return None
