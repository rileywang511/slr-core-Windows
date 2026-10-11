from __future__ import annotations

from slr.config import HttpConfig
from slr.sources.openalex import OpenAlexSource


def test_source_request_uses_configured_retry_policy(monkeypatch):
    captured = {}

    def fake_request(client, method, url, **kwargs):
        captured.update({"client": client, "method": method, "url": url, **kwargs})
        return "response"

    monkeypatch.setattr("slr.sources.base.request_with_retries", fake_request)
    client = object()
    source = OpenAlexSource(
        http_client=client,
        http_config=HttpConfig(max_retries=2, base_delay=0.25, max_delay=4),
    )

    assert source.request("GET", "https://example.test", params={"q": "x"}) == "response"
    assert captured == {
        "client": client,
        "method": "GET",
        "url": "https://example.test",
        "max_retries": 2,
        "base_delay": 0.25,
        "max_delay": 4,
        "params": {"q": "x"},
    }
