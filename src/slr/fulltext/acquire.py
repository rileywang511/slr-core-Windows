from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from slr.audit import utc_now_iso
from slr.config import FetchConfig
from slr.fulltext.naming import target_basename
from slr.fulltext.resolvers import RESOLVER_REGISTRY, load_custom_resolver
from slr.fulltext.resolvers.base import Resolver
from slr import frames


@dataclass
class AttemptResult:
    record_id: int
    source: str
    url: str | None
    http_status: int | None
    bytes: int
    pdf_sha256: str | None
    pdf_path: str | None
    attempted_at: str
    error: str | None
    resolver_metadata_json: str | None


class Acquirer:
    """Orchestrates the resolver chain + PDF download.

    For each final record, attempts each configured resolver in order until one
    returns a URL that yields a real PDF. Records every attempt to
    `fulltext_attempts` for full auditability.
    """

    def __init__(
        self,
        review_dir: Path,
        config: FetchConfig,
        contact_email: str | None = None,
        api_keys: dict[str, str] | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.review_dir = Path(review_dir)
        self.config = config
        self.contact_email = contact_email
        self.api_keys = api_keys or {}
        self.http = http_client or httpx.Client(timeout=60.0, follow_redirects=True)
        self.pdf_dir = self.review_dir / config.cache_dir
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

    def acquire_all(
        self,
        records: list[dict[str, Any]],
        progress: Any = None,
    ) -> list[AttemptResult]:
        """Run the resolver chain for each final record.

        `progress` is an optional callback invoked once per record after its
        chain finishes. Event payload:
          {"kind": "record_done", "idx": N, "record_id": ...,
           "status": "downloaded"|"missing", "via": "<resolver>"|None,
           "attempts_for_record": int}
        """
        all_results: list[AttemptResult] = []
        for i, record in enumerate(records, start=1):
            res = self.acquire_one(record)
            all_results.extend(res)
            if progress:
                success = next((a for a in res if a.pdf_sha256), None)
                progress({
                    "kind": "record_done",
                    "idx": i,
                    "total": len(records),
                    "record_id": record.get("record_id"),
                    "status": "downloaded" if success else "missing",
                    "via": success.source if success else None,
                    "attempts_for_record": len(res),
                })
        return all_results

    def acquire_one(self, record: dict[str, Any]) -> list[AttemptResult]:
        record_id = record["record_id"]
        target_name = target_basename(
            record_id=record_id,
            doi=record.get("doi"),
            title=record.get("title"),
            year=record.get("year"),
        )
        attempts: list[AttemptResult] = []
        for resolver in self._build_resolvers():
            result = resolver.resolve(record)
            if result.url is None:
                attempts.append(
                    AttemptResult(
                        record_id=record_id,
                        source=resolver.name,
                        url=None,
                        http_status=None,
                        bytes=0,
                        pdf_sha256=None,
                        pdf_path=None,
                        attempted_at=utc_now_iso(),
                        error=result.error,
                        resolver_metadata_json=_sanitized_metadata(result.raw),
                    )
                )
                self._persist(attempts[-1])
                continue
            download = self._download(
                result.url, target_name=target_name, cookies=result.request_cookies
            )
            attempt = AttemptResult(
                record_id=record_id,
                source=resolver.name,
                url=result.url,
                http_status=download["status"],
                bytes=download["bytes"],
                pdf_sha256=download["sha256"],
                pdf_path=download["path"],
                attempted_at=utc_now_iso(),
                error=download["error"],
                resolver_metadata_json=_sanitized_metadata(result.raw),
            )
            attempts.append(attempt)
            self._persist(attempt)
            if attempt.pdf_sha256:
                break  # success — stop the chain
        return attempts

    # ---- internal ----

    def _build_resolvers(self) -> list[Resolver]:
        resolvers: list[Resolver] = []
        unknown = sorted(set(self.config.sources) - set(RESOLVER_REGISTRY))
        if unknown:
            raise ValueError(
                "unknown full-text resolver(s): " + ", ".join(unknown)
                + "; built-ins: " + ", ".join(sorted(RESOLVER_REGISTRY))
                + "; add third-party classes through fetch.custom_resolvers"
            )
        if "proxy" in self.config.sources:
            raise ValueError("configure the proxy with fetch.proxy.enabled, not fetch.sources")
        for name in self.config.sources:
            if name == "proxy":
                continue
            cls = RESOLVER_REGISTRY.get(name)
            kwargs: dict[str, Any] = {
                "contact_email": self.contact_email,
                "http_client": self.http,
            }
            api_key = self.api_keys.get(name)
            if api_key:
                kwargs["api_key"] = api_key
            resolvers.append(cls(**kwargs))
        if self.config.proxy.enabled and self.config.proxy.url:
            from slr.fulltext.resolvers.proxy import ProxyResolver
            resolvers.append(
                ProxyResolver(
                    contact_email=self.contact_email,
                    http_client=self.http,
                    url_template=self.config.proxy.url,
                    cookie_jar=self.config.proxy.cookie_jar,
                )
            )
        for spec in self.config.custom_resolvers:
            cls = load_custom_resolver(spec)
            resolvers.append(cls(contact_email=self.contact_email, http_client=self.http))
        return resolvers

    def _download(
        self, url: str, target_name: str, cookies: httpx.Cookies | None = None
    ) -> dict[str, Any]:
        try:
            with self.http.stream("GET", url, cookies=cookies) as resp:
                status = resp.status_code
                if status >= 400:
                    return {"status": status, "bytes": 0, "sha256": None, "path": None, "error": f"http {status}"}
                content = bytearray()
                limit = self.config.max_mb_per_pdf * 1024 * 1024
                for chunk in resp.iter_bytes():
                    content.extend(chunk)
                    if len(content) > limit:
                        return {
                            "status": status, "bytes": len(content), "sha256": None,
                            "path": None, "error": f"exceeded max_mb_per_pdf={self.config.max_mb_per_pdf}",
                        }
                data = bytes(content)
        except Exception as exc:
            return {"status": None, "bytes": 0, "sha256": None, "path": None, "error": f"download error: {exc}"}

        if not data.startswith(b"%PDF-"):
            return {"status": status, "bytes": len(data), "sha256": None, "path": None,
                    "error": "response is not a PDF"}

        sha = hashlib.sha256(data).hexdigest()
        # Filename is the record's predictable target_name (so manual + auto
        # downloads share the same scheme). pdf_sha256 stays = sha256 of bytes,
        # used as a content integrity check.
        rel_path = Path(self.config.cache_dir) / target_name
        abs_path = self.review_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        frames.atomic_write_bytes(abs_path, data)
        return {"status": status, "bytes": len(data), "sha256": sha, "path": rel_path.as_posix(), "error": None}

    def _persist(self, a: AttemptResult) -> None:
        frames.append_fulltext_attempt(self.review_dir, {
            "record_id": a.record_id,
            "source": a.source,
            "url": a.url,
            "http_status": a.http_status,
            "bytes": a.bytes,
            "pdf_sha256": a.pdf_sha256,
            "pdf_path": a.pdf_path,
            "attempted_at": a.attempted_at,
            "error": a.error,
            "resolver_metadata_json": a.resolver_metadata_json,
        })


_SENSITIVE_METADATA_KEY = re.compile(r"(?:api[_-]?key|authorization|cookie|password|secret|token)", re.I)


def _sanitized_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Render lookup evidence safely for CSV: retain facts, never credentials."""
    if metadata is None:
        return None

    def clean(value: Any, key: str | None = None) -> Any:
        if key and _SENSITIVE_METADATA_KEY.search(key):
            return "[redacted]"
        if isinstance(value, dict):
            return {str(k): clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if isinstance(value, str):
            parsed = urlsplit(value)
            if parsed.scheme and parsed.query:
                query = [
                    (name, "[redacted]" if _SENSITIVE_METADATA_KEY.search(name) else item)
                    for name, item in parse_qsl(parsed.query, keep_blank_values=True)
                ]
                return urlunsplit((
                    parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment,
                ))
        return value

    rendered = json.dumps(clean(metadata), sort_keys=True, default=str)
    if len(rendered) > 16_384:
        return json.dumps({"truncated": True, "sha256": hashlib.sha256(rendered.encode()).hexdigest()})
    return rendered
