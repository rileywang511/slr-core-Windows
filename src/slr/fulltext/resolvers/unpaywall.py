from __future__ import annotations

from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


BASE_URL = "https://api.unpaywall.org/v2"


class UnpaywallResolver(Resolver):
    name = "unpaywall"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        doi = record.get("doi")
        if not doi:
            return ResolveResult(error="no DOI")
        if not self.contact_email:
            return ResolveResult(error="Unpaywall requires contact_email")
        try:
            resp = self.http.get(f"{BASE_URL}/{doi}", params={"email": self.contact_email})
        except Exception as exc:
            return ResolveResult(error=f"http error: {exc}")
        if resp.status_code == 404:
            return ResolveResult(error="not found in Unpaywall", raw={"status": 404})
        if resp.status_code >= 400:
            return ResolveResult(error=f"http {resp.status_code}")
        data = resp.json()
        best = data.get("best_oa_location") or {}
        url = best.get("url_for_pdf") or best.get("url")
        if not url:
            return ResolveResult(error="no OA location", raw=data)
        return ResolveResult(url=url, raw=data)
