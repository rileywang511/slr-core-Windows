from __future__ import annotations

import json
from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver


class OpenAlexResolver(Resolver):
    """Reads the OA URL already in the OpenAlex record's stored raw_json (no extra request)."""

    name = "openalex_oa_url"

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        # The final record carries the selected source payload for this resolver.
        # Caller must pass `raw_json` through if available.
        raw_json = record.get("raw_json")
        if not raw_json:
            return ResolveResult(error="no raw_json on final record")
        try:
            raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        except json.JSONDecodeError:
            return ResolveResult(error="raw_json not JSON")
        if raw.get("source") and raw.get("source") != "openalex":
            return ResolveResult(error="final record is not from OpenAlex")
        oa = raw.get("open_access") or {}
        url = oa.get("oa_url")
        if not url:
            primary = raw.get("primary_location") or {}
            url = primary.get("pdf_url")
        if not url:
            return ResolveResult(error="no OA URL in OpenAlex record")
        return ResolveResult(url=url)
