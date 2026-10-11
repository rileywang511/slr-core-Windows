from __future__ import annotations

import os

from slr.llm.base import Provider, ProviderSpec


def make_provider(spec: ProviderSpec) -> Provider:
    """Construct a Provider from a spec. Reads API keys from env."""
    if spec.provider == "anthropic":
        from slr.llm.anthropic import AnthropicProvider
        key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        return AnthropicProvider(api_key=key)
    if spec.provider == "openai":
        from slr.llm.openai import OpenAIProvider
        key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        return OpenAIProvider(api_key=key)
    if spec.provider == "openai-compatible":
        from slr.llm.openai import OpenAIProvider
        key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        return OpenAIProvider(api_key=key, base_url=spec.base_url, compatible=True)
    raise ValueError(f"unknown provider: {spec.provider}")
