"""Datalab Marker API client — convert a PDF to markdown via Datalab's hosted
service (the cloud version of marker-pdf). See https://documentation.datalab.to.

Flow:
  1. POST {base}/convert        (multipart: file + options) -> {request_check_url}
  2. GET  request_check_url     (poll)                      -> status == "complete"
  3. read the "markdown" field

Auth: `X-API-Key: <DATALAB_API_KEY>`. httpx is already a hard dependency.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from slr.config import DatalabConfig


class DatalabError(RuntimeError):
    """Raised when Datalab can't convert a document (or the API errors)."""


def get_api_key(config: DatalabConfig) -> str:
    key = os.environ.get(config.api_key_env)
    if not key:
        raise DatalabError(
            f"{config.api_key_env} not set — required for converter: datalab"
        )
    return key


def run_datalab(
    pdf_path: Path,
    config: DatalabConfig,
    *,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, int | None]:
    """Convert a PDF with Datalab. Returns (markdown_text, page_count)."""
    key = api_key or get_api_key(config)
    headers = {"X-API-Key": key}
    owns_client = client is None
    http = client or httpx.Client(timeout=config.timeout_s)
    try:
        check_url = _submit(http, headers, pdf_path, config)
        run = _poll(http, headers, check_url, config)
        return run.get("markdown") or "", run.get("page_count")
    except httpx.HTTPError as exc:
        raise DatalabError(f"Datalab API request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


def _submit(http: httpx.Client, headers: dict, pdf_path: Path, config: DatalabConfig) -> str:
    data: dict[str, str] = {"output_format": "markdown", "mode": config.mode}
    if config.max_pages is not None:
        data["max_pages"] = str(config.max_pages)
    with open(pdf_path, "rb") as fh:
        resp = http.post(
            f"{config.base_url}/convert",
            headers=headers,
            data=data,
            files={"file": (pdf_path.name, fh, "application/pdf")},
        )
    resp.raise_for_status()
    body = resp.json()
    if body.get("success") is False:
        raise DatalabError(body.get("error") or "Datalab submission failed")
    check_url = body.get("request_check_url")
    if not check_url:
        request_id = body.get("request_id")
        if not request_id:
            raise DatalabError("Datalab response had no request_check_url or request_id")
        check_url = f"{config.base_url}/convert/{request_id}"
    return check_url


def _poll(http: httpx.Client, headers: dict, check_url: str, config: DatalabConfig) -> dict:
    deadline = time.monotonic() + config.timeout_s
    while True:
        if time.monotonic() > deadline:
            raise DatalabError(f"Datalab conversion timed out after {config.timeout_s}s")
        time.sleep(config.poll_interval_s)
        resp = http.get(check_url, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        status = body.get("status")
        if status == "complete":
            if body.get("success") is False or body.get("error"):
                raise DatalabError(body.get("error") or "Datalab conversion failed")
            return body
        if body.get("success") is False or body.get("error"):
            raise DatalabError(body.get("error") or "Datalab conversion failed")
