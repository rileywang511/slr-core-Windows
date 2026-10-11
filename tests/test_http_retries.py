from __future__ import annotations

import httpx
import pytest

from slr import http as slr_http
from slr.http import request_with_retries


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip the actual sleeps during retry tests."""
    monkeypatch.setattr(slr_http.time, "sleep", lambda s: None)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_succeeds_first_try():
    calls = []
    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"ok": True})
    resp = request_with_retries(_client(handler), "GET", "https://x/y")
    assert resp.status_code == 200
    assert len(calls) == 1


def test_retries_on_500_then_succeeds():
    seq = [500, 500, 200]
    calls = []
    def handler(req):
        status = seq[len(calls)]
        calls.append(status)
        return httpx.Response(status, json={"n": status})
    events = []
    resp = request_with_retries(
        _client(handler), "GET", "https://x/y",
        on_event=events.append,
    )
    assert resp.status_code == 200
    assert calls == [500, 500, 200]
    assert sum(1 for e in events if e["kind"] == "retry") == 2


def test_retries_on_429_and_respects_retry_after():
    calls = []
    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200)
    events = []
    request_with_retries(
        _client(handler), "GET", "https://x/y",
        on_event=events.append,
    )
    retry_delays = [e["delay"] for e in events if e["kind"] == "retry"]
    assert retry_delays == [7.0, 7.0]


def test_does_not_retry_on_404():
    calls = []
    def handler(req):
        calls.append(1)
        return httpx.Response(404)
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(_client(handler), "GET", "https://x/y")
    assert len(calls) == 1


def test_gives_up_after_max_retries():
    calls = []
    def handler(req):
        calls.append(1)
        return httpx.Response(503)
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retries(_client(handler), "GET", "https://x/y", max_retries=2)
    assert len(calls) == 3   # 1 initial + 2 retries


def test_retries_on_network_error():
    calls = []
    def handler(req):
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("network blip")
        return httpx.Response(200)
    events = []
    resp = request_with_retries(
        _client(handler), "GET", "https://x/y",
        on_event=events.append,
    )
    assert resp.status_code == 200
    assert any(e["kind"] == "retry" and "network" in e["reason"] for e in events)


def test_network_error_give_up_event():
    calls = []
    def handler(req):
        calls.append(1)
        raise httpx.ConnectError("perma-broken")
    events = []
    with pytest.raises(httpx.ConnectError):
        request_with_retries(
            _client(handler), "GET", "https://x/y", max_retries=1,
            on_event=events.append,
        )
    assert any(e["kind"] == "give_up" for e in events)
