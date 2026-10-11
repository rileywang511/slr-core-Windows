from __future__ import annotations

import json
from typing import Any

from slr.fulltext.resolvers.base import ResolveResult, Resolver
from slr.fulltext.cookies import load_netscape_cookie_jar


class ProxyResolver(Resolver):
    """Institutional EZproxy / OpenAthens proxy.

    Wraps the canonical landing-page URL of the record in the configured proxy
    template, e.g. https://login.ezproxy.cranfield.ac.uk/login?url={target}.
    Requires the user to have legitimate institutional access and to have
    obtained a cookie jar for the proxy in advance.

    This resolver does NOT log in for the user. It accepts a browser-exported
    Netscape cookie jar, keeps it in memory, and applies it only to this proxy
    request. Jar paths and cookie values are never written to review evidence.
    """

    name = "proxy"

    def __init__(self, contact_email: str | None = None, http_client: Any = None,
                 url_template: str | None = None, cookie_jar: str | None = None, **kwargs: Any):
        super().__init__(contact_email=contact_email, http_client=http_client, **kwargs)
        self.url_template = url_template
        self.request_cookies = load_netscape_cookie_jar(cookie_jar) if cookie_jar else None

    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        if not self.url_template:
            return ResolveResult(error="proxy not configured")
        # Prefer the DOI-resolver URL since publishers redirect from there.
        doi = record.get("doi")
        if doi:
            target = f"https://doi.org/{doi}"
        else:
            # Try record raw_json for a landing URL
            raw_json = record.get("raw_json")
            target = None
            if raw_json:
                try:
                    raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                    target = raw.get("url") or raw.get("primary_location", {}).get("landing_page_url")
                except json.JSONDecodeError:
                    pass
            if not target:
                return ResolveResult(error="no DOI or landing URL to proxy")
        url = self.url_template.format(target=target)
        return ResolveResult(url=url, raw={"target": target}, request_cookies=self.request_cookies)
