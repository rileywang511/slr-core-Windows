from __future__ import annotations

from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


BASE_URL = "https://api.core.ac.uk/v3"


class CoreResolver(Resolver):
    """CORE.ac.uk — aggregator of institutional OA repositories.

    Requires an API key (free, register at https://core.ac.uk/services/api).
    Reads CORE_API_KEY from constructor's api_key arg.
    """

    name = "core"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        if not self.api_key:
            return ResolveResult(error="CORE requires api_key")
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            resp = self.http.get(
                f"{BASE_URL}/search/works",
                params={"q": f"doi:{doi}", "limit": 1},
                headers=headers,
            )
        except Exception as exc:
            return ResolveResult(error=f"http error: {exc}")
        if resp.status_code >= 400:
            return ResolveResult(error=f"http {resp.status_code}")
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return ResolveResult(error="not found")
        work = results[0]
        url = work.get("downloadUrl") or work.get("fullTextLink")
        if not url:
            return ResolveResult(error="no downloadUrl", raw=work)
        return ResolveResult(url=url, raw=work)
