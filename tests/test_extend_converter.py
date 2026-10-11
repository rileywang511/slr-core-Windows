"""Extend Parse 2.0 client — upload, parse run, poll, concatenate chunks.

No network: a fake httpx client is injected via run_extend(client=...).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slr.config import ExtendConfig
from slr.fulltext.extend import ExtendError, get_api_key, run_extend


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, final):
        self.final = final
        self.poll_calls = 0
        self.uploaded = False
        self.closed = False

    def post(self, url, headers=None, files=None, json=None):
        if url.endswith("/files/upload"):
            assert files and "file" in files
            self.uploaded = True
            return _Resp({"id": "file_123", "name": "doc.pdf"})
        if url.endswith("/parse_runs"):
            assert json["file"]["id"] == "file_123"
            assert json["config"]["target"] == "markdown"
            return _Resp({"id": "pr_1", "status": "PROCESSING"})
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None):
        assert url.endswith("/parse_runs/pr_1")
        self.poll_calls += 1
        if self.poll_calls < 2:
            return _Resp({"id": "pr_1", "status": "PROCESSING"})
        return _Resp(self.final)

    def close(self):
        self.closed = True


def _cfg() -> ExtendConfig:
    return ExtendConfig(poll_interval_s=0, timeout_s=10)


def _pdf(tmp_path: Path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 data")
    return p


def test_run_extend_concatenates_chunk_content(tmp_path):
    fake = _FakeClient({
        "id": "pr_1", "status": "PROCESSED",
        "output": {"chunks": [{"content": "page one"}, {"content": "page two"}]},
        "metrics": {"pageCount": 2},
    })
    text, pages = run_extend(_pdf(tmp_path), _cfg(), api_key="k", client=fake)
    assert text == "page one\n\npage two"
    assert pages == 2
    assert fake.uploaded and fake.poll_calls == 2


def test_run_extend_raises_on_failed_status(tmp_path):
    fake = _FakeClient({
        "id": "pr_1", "status": "FAILED",
        "failureReason": "UNSUPPORTED", "failureMessage": "encrypted PDF",
    })
    with pytest.raises(ExtendError, match="encrypted PDF"):
        run_extend(_pdf(tmp_path), _cfg(), api_key="k", client=fake)


def test_get_api_key_requires_env(monkeypatch):
    monkeypatch.delenv("EXTEND_API_KEY", raising=False)
    with pytest.raises(ExtendError, match="EXTEND_API_KEY"):
        get_api_key(ExtendConfig())
    monkeypatch.setenv("EXTEND_API_KEY", "secret")
    assert get_api_key(ExtendConfig()) == "secret"
