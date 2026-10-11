"""Extend Parse 2.0 client — convert a PDF to markdown via the Extend API.

Flow (https://docs.extend.ai):
  1. POST /files/upload   (multipart)            -> file id
  2. POST /parse_runs     {file:{id}, config}    -> parse run id (async)
  3. GET  /parse_runs/{id}  (poll)               -> until status PROCESSED/FAILED
  4. concatenate output.chunks[].content         -> markdown text

Auth: `Authorization: Bearer <EXTEND_API_KEY>` + `x-extend-api-version` header.
httpx is already a hard dependency, so there is nothing extra to install.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from slr.config import ExtendConfig

EXTEND_BASE_URL = "https://api.extend.ai"


class ExtendError(RuntimeError):
    """Raised when Extend can't parse a document (or the API errors)."""


def get_api_key(config: ExtendConfig) -> str:
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ExtendError(
            f"{config.api_key_env} not set — required for converter: extend"
        )
    return key


def run_extend(
    pdf_path: Path,
    config: ExtendConfig,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, int | None]:
    """Parse a PDF with Extend. Returns (markdown_text, page_count)."""
    key = api_key or get_api_key(config)
    headers = {
        "Authorization": f"Bearer {key}",
        "x-extend-api-version": config.api_version,
    }
    owns_client = client is None
    http = client or httpx.Client(timeout=config.timeout_s)
    try:
        file_id = _upload(http, headers, pdf_path)
        run = _create_parse_run(http, headers, file_id, config.target)
        run = _poll(http, headers, run, config)
        return _extract_text(run)
    except httpx.HTTPError as exc:
        raise ExtendError(f"Extend API request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def _upload(http: httpx.Client, headers: dict, pdf_path: Path) -> str:
    with open(pdf_path, "rb") as fh:
        resp = http.post(
            f"{EXTEND_BASE_URL}/files/upload",
            headers=headers,
            files={"file": (pdf_path.name, fh, "application/pdf")},
        )
    resp.raise_for_status()
    file_id = resp.json().get("id")
    if not file_id:
        raise ExtendError("Extend /files/upload did not return a file id")
    return file_id


def _create_parse_run(http: httpx.Client, headers: dict, file_id: str, target: str) -> dict:
    resp = http.post(
        f"{EXTEND_BASE_URL}/parse_runs",
        headers={**headers, "Content-Type": "application/json"},
        json={"file": {"id": file_id}, "config": {"target": target}},
    )
    resp.raise_for_status()
    return resp.json()


def _poll(http: httpx.Client, headers: dict, run: dict, config: ExtendConfig) -> dict:
    run_id = run.get("id")
    status = run.get("status")
    deadline = time.monotonic() + config.timeout_s
    while status not in ("PROCESSED", "FAILED"):
        if time.monotonic() > deadline:
            raise ExtendError(f"Extend parse timed out after {config.timeout_s}s (run {run_id})")
        time.sleep(config.poll_interval_s)
        resp = http.get(f"{EXTEND_BASE_URL}/parse_runs/{run_id}", headers=headers)
        resp.raise_for_status()
        run = resp.json()
        status = run.get("status")
    if status != "PROCESSED":
        raise ExtendError(
            run.get("failureMessage") or run.get("failureReason") or "Extend parse FAILED"
        )
    return run


def _extract_text(run: dict) -> tuple[str, int | None]:
    output = run.get("output") or {}
    chunks = output.get("chunks") or []
    text = "\n\n".join(c.get("content", "") for c in chunks if c.get("content"))
    pages = (run.get("metrics") or {}).get("pageCount")
    return text, pages
