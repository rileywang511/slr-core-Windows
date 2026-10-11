"""Retry-with-backoff helper for source HTTP requests.

Sources call `request_with_retries(client, "GET", url, params=...)` instead of
`client.get(url, params=...)` so transient failures don't crash a long search.

Retries on:
  - HTTP 5xx (server-side)
  - HTTP 429 (rate-limit) — respects Retry-After when present
  - httpx.RequestError (timeouts, connection drops, DNS issues)

Does NOT retry on:
  - 4xx (except 429) — these are bugs we can't fix by waiting
  - Out-of-budget after max_retries
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable

import httpx

RetryEvent = Callable[[dict[str, Any]], None]


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    on_event: RetryEvent | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP request, retrying transient failures with exponential backoff.

    Returns the final Response on success. Raises the final exception or
    response.raise_for_status() if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt == max_retries:
                if on_event:
                    on_event({"kind": "give_up", "attempt": attempt, "error": str(exc)})
                raise
            delay = _sleep_for(attempt, base_delay, max_delay)
            if on_event:
                on_event({"kind": "retry", "attempt": attempt, "delay": delay,
                          "reason": f"network: {exc!s}"})
            time.sleep(delay)
            continue

        if resp.status_code < 400:
            return resp

        retriable = resp.status_code >= 500 or resp.status_code == 429
        if not retriable or attempt == max_retries:
            resp.raise_for_status()
            return resp  # unreachable

        delay = _retry_after_or_backoff(resp, attempt, base_delay, max_delay)
        if on_event:
            on_event({"kind": "retry", "attempt": attempt, "delay": delay,
                      "status": resp.status_code,
                      "reason": f"HTTP {resp.status_code}"})
        try:
            resp.close()
        except Exception:
            pass
        time.sleep(delay)

    # Defensive — should be unreachable
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("request_with_retries exited loop without returning")


def _retry_after_or_backoff(
    resp: httpx.Response, attempt: int, base_delay: float, max_delay: float
) -> float:
    """Honour Retry-After if the server set it; otherwise exponential backoff."""
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), max_delay)
        except ValueError:
            pass  # Retry-After can be HTTP-date; fall through to backoff
    return _sleep_for(attempt, base_delay, max_delay)


def _sleep_for(attempt: int, base_delay: float, max_delay: float) -> float:
    """Exponential backoff with full jitter."""
    expo = base_delay * (2 ** attempt)
    capped = min(expo, max_delay)
    return random.uniform(0.0, capped)
