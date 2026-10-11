from __future__ import annotations

import json
from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


class EuropePMCResolver(Resolver):
    """If the record has a PMCID, point at the OA PDF endpoint.

    Europe PMC OA PDFs are served from:
      https://europepmc.org/articles/<PMCID>?pdf=render
    """

    name = "europepmc_oa"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        # Try to find a PMCID. It may be present on the final row or in raw_json.
        pmcid = record.get("pmcid")
        if not pmcid:
            raw_json = record.get("raw_json")
            if raw_json:
                try:
                    raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                    pmcid = raw.get("pmcid") or (raw.get("ids") or {}).get("pmcid")
                except json.JSONDecodeError:
                    pass
        if not pmcid:
            return ResolveResult(error="no PMCID")
        if isinstance(pmcid, str) and pmcid.startswith("http"):
            pmcid = pmcid.rstrip("/").split("/")[-1]
        if not isinstance(pmcid, str) or not pmcid.upper().startswith("PMC"):
            return ResolveResult(error=f"invalid PMCID: {pmcid!r}")
        url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        return ResolveResult(url=url)
