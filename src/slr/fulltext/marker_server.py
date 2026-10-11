"""Self-hosted marker conversion server + its client.

Run the server on a machine that has marker-pdf installed (ideally a GPU box):

    MARKER_SERVER_KEY=secret slr marker-server --host 0.0.0.0 --port 8000

Then screen from anywhere — no marker / torch needed on the screening machine:

    export MARKER_SERVER_URL=http://gpu-box:8000
    export MARKER_SERVER_KEY=secret
    slr screen <dir> --stage full_text --converter server

HTTP protocol (deliberately tiny — both ends are ours):
    POST /convert   body = raw PDF bytes
        headers: Authorization: Bearer <key>   (only if the server has a key)
                 X-Output-Format, X-Force-OCR, X-Use-LLM, X-Filename
        -> 200 {"text": "...", "pages": N}
        -> 4xx/5xx {"error": "..."}
    GET  /health    -> 200 {"status": "ok"}

The server runs marker through the same recycled child-process pool the local
converter uses, so its own memory stays bounded over long batches.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import httpx

from slr.config import ServerConfig
from slr.fulltext.marker_worker import MarkerSubprocess


class ServerError(RuntimeError):
    """Raised when the self-hosted marker server can't be reached or fails."""


# --------------------------------------------------------------------------
# Client (used by the `server` converter during screening)
# --------------------------------------------------------------------------

def get_endpoint(config: ServerConfig) -> str:
    url = os.environ.get(config.url_env)
    if not url:
        raise ServerError(
            f"{config.url_env} not set — required for converter: server "
            f"(point it at your `slr marker-server`)"
        )
    return url.rstrip("/")


def convert_via_server(
    pdf_path: Path,
    config: ServerConfig,
    *,
    client: httpx.Client | None = None,
) -> tuple[str, int | None]:
    """Send a PDF to the self-hosted marker server. Returns (text, page_count)."""
    url = get_endpoint(config)
    key = os.environ.get(config.api_key_env)
    headers = {
        "Content-Type": "application/pdf",
        "X-Output-Format": config.output_format,
        "X-Force-OCR": str(config.force_ocr).lower(),
        "X-Use-LLM": str(config.use_llm).lower(),
        # HTTP header values must be ASCII; the server treats this as metadata.
        "X-Filename": quote(Path(pdf_path).name),
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    owns_client = client is None
    http = client or httpx.Client(timeout=config.timeout_s)
    try:
        resp = http.post(f"{url}/convert", headers=headers, content=Path(pdf_path).read_bytes())
        if resp.status_code != 200:
            msg = None
            try:
                msg = resp.json().get("error")
            except Exception:
                pass
            raise ServerError(msg or f"marker server returned HTTP {resp.status_code}")
        body = resp.json()
        return body.get("text") or "", body.get("pages")
    except httpx.HTTPError as exc:
        raise ServerError(f"marker server request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

def build_httpd(host: str, port: int, recycle_after: int = 50) -> ThreadingHTTPServer:
    """Construct (but do not start) the marker HTTP server."""
    key = os.environ.get("MARKER_SERVER_KEY")
    lock = threading.Lock()
    pools: dict[tuple[str, bool, bool], MarkerSubprocess] = {}

    def _pool_for(output_format: str, use_llm: bool, force_ocr: bool) -> MarkerSubprocess:
        k = (output_format, use_llm, force_ocr)
        pool = pools.get(k)
        if pool is None:
            pool = MarkerSubprocess(
                output_format=output_format, use_llm=use_llm,
                force_ocr=force_ocr, recycle_after=recycle_after,
            )
            pools[k] = pool
        return pool

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/").endswith("/health") or self.path.rstrip("/") == "":
                self._reply(200, {"status": "ok"})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/convert":
                self._reply(404, {"error": "not found"})
                return
            if key and not _bearer_ok(self.headers.get("Authorization", ""), key):
                self._reply(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else b""
            if not data:
                self._reply(400, {"error": "empty request body"})
                return
            output_format = self.headers.get("X-Output-Format", "markdown")
            force_ocr = self.headers.get("X-Force-OCR", "false").lower() == "true"
            use_llm = self.headers.get("X-Use-LLM", "false").lower() == "true"
            tmp = tempfile.NamedTemporaryFile(prefix="slr-srv-", suffix=".pdf", delete=False)
            tmp.write(data)
            tmp.close()
            try:
                with lock:  # one marker pool, serialize conversions
                    text, pages = _pool_for(output_format, use_llm, force_ocr).convert(Path(tmp.name))
                self._reply(200, {"text": text, "pages": pages})
            except Exception as exc:  # noqa: BLE001 — report any failure to the client
                self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        def log_message(self, *args) -> None:  # keep stdout/err clean
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd._marker_pools = pools  # type: ignore[attr-defined]  # for shutdown cleanup
    httpd._marker_auth = bool(key)  # type: ignore[attr-defined]
    return httpd


def serve(host: str = "0.0.0.0", port: int = 8000, recycle_after: int = 50) -> None:
    httpd = build_httpd(host, port, recycle_after)
    auth = "on" if getattr(httpd, "_marker_auth", False) else "OFF (set MARKER_SERVER_KEY)"
    sys.stderr.write(f"[marker-server] listening on {host}:{port} — auth {auth}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for pool in getattr(httpd, "_marker_pools", {}).values():
            pool.close()
        httpd.server_close()


def _bearer_ok(auth_header: str, key: str) -> bool:
    prefix = "Bearer "
    if not auth_header.startswith(prefix):
        return False
    return hmac.compare_digest(auth_header[len(prefix):], key)
