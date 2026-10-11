from __future__ import annotations

import pytest

from slr.llm.base import ProviderSpec
from slr.llm.factory import make_provider


def test_factory_unknown_provider_raises():
    with pytest.raises(ValueError):
        make_provider(ProviderSpec(provider="nope", model="x"))  # type: ignore[arg-type]


def test_factory_anthropic_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Will raise on construction (no key) — confirms the factory plumbed through
    with pytest.raises((RuntimeError, ImportError)):
        make_provider(ProviderSpec(provider="anthropic", model="claude-x"))


def test_factory_openai_compatible_routes_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # OpenAI-compatible accepts missing key; should not raise on construction.
    # If openai SDK isn't installed, it raises ImportError — that's also a valid
    # confirmation the factory routed correctly.
    try:
        prov = make_provider(ProviderSpec(
            provider="openai-compatible",
            model="llama-3",
            base_url="http://localhost:11434/v1",
        ))
    except ImportError:
        pytest.skip("openai SDK not installed")
        return
    assert prov.name == "openai-compatible"
    assert prov.base_url == "http://localhost:11434/v1"
