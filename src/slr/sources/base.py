from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from slr.config import HttpConfig
from slr.http import request_with_retries
from slr.query.ast import Expr
from slr.record import Record


@dataclass
class SourceResult:
    raw_records: list[dict[str, Any]] = field(default_factory=list)
    query_translated: str = ""
    warnings: list[str] = field(default_factory=list)


ProgressFn = Callable[[dict[str, Any]], None]


class Source(ABC):
    name: str

    def __init__(
        self,
        contact_email: str | None = None,
        http_client: Any = None,
        http_config: HttpConfig | None = None,
    ):
        self.contact_email = contact_email
        self.http = http_client  # injected for tests; real impls instantiate httpx.Client
        self.http_config = http_config or HttpConfig()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Request using this review's configured retry policy."""
        if self.http is None:
            raise RuntimeError("source HTTP client was not initialised")
        return request_with_retries(
            self.http, method, url,
            max_retries=self.http_config.max_retries,
            base_delay=self.http_config.base_delay,
            max_delay=self.http_config.max_delay,
            **kwargs,
        )

    @abstractmethod
    def search(
        self,
        expr: Expr,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int | None = None,
        progress: ProgressFn | None = None,
    ) -> SourceResult: ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> Record: ...

    def search_and_normalize(
        self,
        expr: Expr,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int | None = None,
        progress: ProgressFn | None = None,
    ) -> tuple[SourceResult, list[Record]]:
        result = self.search(expr, date_from=date_from, date_to=date_to,
                             limit=limit, progress=progress)
        records = [self.normalize(r) for r in result.raw_records]
        return result, records
