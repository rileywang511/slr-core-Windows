from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ResolveResult:
    url: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None
    # Request-only data, deliberately excluded from persisted resolver metadata.
    request_cookies: httpx.Cookies | None = None


class Resolver(ABC):
    """Find one legitimate PDF URL for a final record.

    Implementations only discover a candidate URL; the acquisition orchestrator
    owns downloading, PDF validation, persistence, retry/reporting, and resolver
    ordering. This is the extension boundary for new acquisition services.
    """

    name: str

    def __init__(self, contact_email: str | None = None, api_key: str | None = None,
                 http_client: httpx.Client | None = None, **kwargs: Any):
        self.contact_email = contact_email
        self.api_key = api_key
        self.http = http_client or httpx.Client(timeout=20.0, follow_redirects=True)
        self.kwargs = kwargs

    @abstractmethod
    def resolve(self, record: dict[str, Any]) -> ResolveResult:
        """Return a candidate URL or a user-facing failure reason.

        Never download/write files here. Include service metadata in ``raw``
        when it helps audit a successful or failed lookup.
        """
        ...
