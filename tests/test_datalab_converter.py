"""Datalab Marker API client — submit, poll, read markdown. No network: a fake
httpx client is injected via run_datalab(client=...)."""

from __future__ import annotations

from pathlib import Path

import pytest

from slr.config import DatalabConfig
from slr.fulltext.datalab import DatalabError, get_api_key, run_datalab


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
        self.submitted = False
        self.polls = 0

    def post(self, url, headers=None, data=None, files=None):
        assert url.endswith("/convert")
        assert headers["X-API-Key"] == "k"
        assert data["output_format"] == "markdown"
        assert data["mode"] == "balanced"
        assert "file" in files
        self.submitted = True
        return _Resp({"success": True, "request_id": "req_1",
                      "request_check_url": "https://www.datalab.to/api/v1/convert/req_1"})

    def get(self, url, headers=None):
        assert url.endswith("/convert/req_1")
        self.polls += 1
        if self.polls < 2:
            return _Resp({"status": "processing"})
        return _Resp(self.final)

    def close(self):
        pass


def _cfg() -> DatalabConfig:
    return DatalabConfig(poll_interval_s=0, timeout_s=10)


def _pdf(tmp_path: Path) -> Path:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 data")
    return p


def test_run_datalab_returns_markdown(tmp_path):
    fake = _FakeClient({"status": "complete", "markdown": "# Title\n\nbody", "page_count": 4})
    text, pages = run_datalab(_pdf(tmp_path), _cfg(), api_key="k", client=fake)
    assert text == "# Title\n\nbody"
    assert pages == 4
    assert fake.submitted and fake.polls == 2


def test_run_datalab_raises_on_error_status(tmp_path):
    fake = _FakeClient({"status": "complete", "success": False, "error": "bad scan"})
    with pytest.raises(DatalabError, match="bad scan"):
        run_datalab(_pdf(tmp_path), _cfg(), api_key="k", client=fake)


def test_run_datalab_raises_on_failure_midpoll(tmp_path):
    fake = _FakeClient({"error": "rate limited"})
    with pytest.raises(DatalabError, match="rate limited"):
        run_datalab(_pdf(tmp_path), _cfg(), api_key="k", client=fake)


def test_get_api_key_requires_env(monkeypatch):
    monkeypatch.delenv("DATALAB_API_KEY", raising=False)
    with pytest.raises(DatalabError, match="DATALAB_API_KEY"):
        get_api_key(DatalabConfig())
    monkeypatch.setenv("DATALAB_API_KEY", "secret")
    assert get_api_key(DatalabConfig()) == "secret"


def test_fulltext_converter_routes_to_datalab(tmp_path, monkeypatch):
    """FulltextConverter(converter='datalab') uses run_datalab and caches output."""
    import slr.fulltext.datalab as datalab_mod
    from slr.config import FetchConfig
    from slr.fulltext.convert import FulltextConverter
    from tests import ReviewFiles

    review = tmp_path / "review"
    review.mkdir()
    store = ReviewFiles(review)
    store.init()
    (review / "pdfs").mkdir()
    pdf = review / "pdfs" / "abc1234567.pdf"
    pdf.write_bytes(b"%PDF-1.4 data")

    monkeypatch.setenv("DATALAB_API_KEY", "test-key")
    monkeypatch.setattr(datalab_mod, "run_datalab", lambda *a, **k: ("# datalab md", 2))

    conv = FulltextConverter(review, "datalab", FetchConfig())
    text, err = conv.convert_text(1, pdf)
    assert err is None and text == "# datalab md"
    # Cached to markdown/<stem>.datalab.md
    assert (review / "markdown" / "abc1234567.datalab.md").exists()
