"""Self-hosted marker server + its client. The server is exercised over real
HTTP with a fake conversion pool (no marker install needed)."""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import pytest

import slr.fulltext.marker_server as ms
from slr.config import ServerConfig
from slr.fulltext.marker_server import ServerError, convert_via_server, get_endpoint


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

def test_get_endpoint_requires_env(monkeypatch):
    monkeypatch.delenv("MARKER_SERVER_URL", raising=False)
    with pytest.raises(ServerError, match="MARKER_SERVER_URL"):
        get_endpoint(ServerConfig())
    monkeypatch.setenv("MARKER_SERVER_URL", "http://gpu-box:8000/")
    assert get_endpoint(ServerConfig()) == "http://gpu-box:8000"  # trailing slash trimmed


def test_convert_via_server_sends_pdf_and_returns_text(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKER_SERVER_URL", "http://gpu-box:8000")
    monkeypatch.setenv("MARKER_SERVER_KEY", "secret")
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"text": "# converted", "pages": 3}

    class _Client:
        def post(self, url, headers=None, content=None):
            captured.update(url=url, headers=headers, content=content)
            return _Resp()

        def close(self):
            pass

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 body")
    text, pages = convert_via_server(pdf, ServerConfig(), client=_Client())

    assert (text, pages) == ("# converted", 3)
    assert captured["url"] == "http://gpu-box:8000/convert"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["headers"]["X-Filename"] == "a.pdf"
    assert captured["content"] == b"%PDF-1.4 body"


def test_convert_via_server_raises_on_error_status(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKER_SERVER_URL", "http://gpu-box:8000")

    class _Resp:
        status_code = 500

        def json(self):
            return {"error": "marker blew up"}

    class _Client:
        def post(self, *a, **k):
            return _Resp()

        def close(self):
            pass

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(ServerError, match="marker blew up"):
        convert_via_server(pdf, ServerConfig(), client=_Client())


def test_server_client_accepts_unicode_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKER_SERVER_URL", "http://gpu-box:8000")
    pdf = tmp_path / "中文 paper 🧪.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def handle(request):
        from urllib.parse import unquote

        assert unquote(request.headers["X-Filename"]) == pdf.name
        assert request.content == pdf.read_bytes()
        return httpx.Response(200, json={"text": "中文内容 🧪", "pages": 1})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert convert_via_server(pdf, ServerConfig(), client=client) == ("中文内容 🧪", 1)


# --------------------------------------------------------------------------
# Server (real HTTP, fake conversion pool)
# --------------------------------------------------------------------------

class _FakePool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def convert(self, path):
        head = Path(path).read_bytes()[:4]
        return f"converted:{head!r}", 7

    def close(self):
        pass


def _start(monkeypatch, *, with_key: bool):
    monkeypatch.setattr(ms, "MarkerSubprocess", _FakePool)
    if with_key:
        monkeypatch.setenv("MARKER_SERVER_KEY", "secret")
    else:
        monkeypatch.delenv("MARKER_SERVER_KEY", raising=False)
    httpd = ms.build_httpd("127.0.0.1", 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{port}"


def test_health_and_authed_convert(monkeypatch):
    httpd, base = _start(monkeypatch, with_key=True)
    try:
        assert httpx.get(f"{base}/health", trust_env=False).json() == {"status": "ok"}
        # No / wrong token -> 401
        assert httpx.post(f"{base}/convert", content=b"%PDF-1.4 x", trust_env=False).status_code == 401
        # Correct token -> 200 with converted text
        r = httpx.post(f"{base}/convert", content=b"%PDF-1.4 x",
                       headers={"Authorization": "Bearer secret"}, trust_env=False)
        assert r.status_code == 200
        body = r.json()
        assert body["pages"] == 7 and "converted" in body["text"]
        # Empty body -> 400
        assert httpx.post(f"{base}/convert", content=b"",
                          headers={"Authorization": "Bearer secret"}, trust_env=False).status_code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_open_server_when_no_key(monkeypatch):
    httpd, base = _start(monkeypatch, with_key=False)
    try:
        r = httpx.post(f"{base}/convert", content=b"%PDF data", trust_env=False)
        assert r.status_code == 200 and r.json()["pages"] == 7
    finally:
        httpd.shutdown()
        httpd.server_close()
