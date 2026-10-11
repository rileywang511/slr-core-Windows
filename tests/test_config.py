from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from slr.config import Config, EXAMPLE_CONFIG, ProxyConfig, load_config, load_config_for_review


def test_missing_config_yields_defaults(tmp_path: Path):
    """An absent config.yaml is OK — defaults take over."""
    cfg = load_config_for_review(tmp_path)
    assert isinstance(cfg, Config)
    assert cfg.search.contact_email is None
    assert cfg.fetch.markdown_dir == "markdown/"
    assert cfg.search.http.max_retries == 5      # default


def test_loads_example_config(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.search.contact_email == "you@example.com"
    assert cfg.screen.title_abstract.provider == "anthropic"
    assert cfg.screen.full_text.provider == "openai-compatible"
    assert cfg.screen.full_text.base_url == "http://localhost:11434/v1"
    assert cfg.screen.full_text.converter == "marker"  # default conversion engine
    assert cfg.fetch.extend.api_key_env == "EXTEND_API_KEY"
    assert cfg.fetch.datalab.api_key_env == "DATALAB_API_KEY"
    assert cfg.fetch.datalab.mode == "balanced"
    assert cfg.fetch.pdf_inspector.use_ocr is False
    assert "openalex_oa_url" in cfg.fetch.sources


def test_converter_defaults_to_marker(tmp_path: Path):
    cfg = load_config_for_review(tmp_path)
    assert cfg.screen.full_text.converter == "marker"
    assert cfg.fetch.extend.target == "markdown"


def test_partial_config_merges_with_defaults(tmp_path: Path):
    """A config.yaml with only a few keys should still parse — the rest stays default."""
    p = tmp_path / "config.yaml"
    p.write_text("search:\n  contact_email: me@example.org\n")
    cfg = load_config(p)
    assert cfg.search.contact_email == "me@example.org"
    # Defaults still apply elsewhere
    assert cfg.fetch.max_mb_per_pdf == 25
    assert cfg.screen.title_abstract.provider is None  # not configured


def test_unknown_config_keys_are_rejected(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("fetch:\n  acquire: true\n")

    with pytest.raises(ValidationError, match="acquire"):
        load_config(p)


@pytest.mark.parametrize("retired_heading", ["screening", "fulltext"])
def test_retired_config_headings_are_rejected(tmp_path: Path, retired_heading: str):
    p = tmp_path / "config.yaml"
    p.write_text(f"{retired_heading}: {{}}\n")

    with pytest.raises(ValidationError, match=retired_heading):
        load_config(p)


def test_proxy_configuration_cannot_be_inert():
    with pytest.raises(ValidationError, match="proxy.url is required"):
        ProxyConfig(enabled=True)
    with pytest.raises(ValidationError, match="proxy.enabled: true"):
        ProxyConfig(url="https://proxy.example/login?url={target}")


def test_pdf_inspector_ocr_options_cannot_be_inert():
    from slr.config import PdfInspectorConfig

    with pytest.raises(ValidationError, match="requires use_ocr"):
        PdfInspectorConfig(model_directory="/models")
    with pytest.raises(ValidationError, match="requires use_ocr"):
        PdfInspectorConfig(offline=True)


def test_config_independent_of_protocol(tmp_path: Path):
    """Protocol and Config don't share fields anymore."""
    from slr.protocol import EXAMPLE_PROTOCOL, load_protocol
    proto_path = tmp_path / "protocol.yaml"
    proto_path.write_text(EXAMPLE_PROTOCOL, encoding="utf-8")
    proto = load_protocol(proto_path)
    # Protocol no longer has these — they moved to Config.
    assert not hasattr(proto, "fulltext")
    assert not hasattr(proto.screen.title_abstract, "provider")
    assert not hasattr(proto.screen.title_abstract, "model")
