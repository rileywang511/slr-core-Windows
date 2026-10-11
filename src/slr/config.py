"""System/environment configuration — kept separate from protocol.yaml.

protocol.yaml is the methodological declaration (query, criteria, screening
*types*) that gets shared with collaborators for PRISMA reproducibility.

config.yaml is per-machine runtime plumbing, grouped by the command it
configures: ``search``, ``screen``, and ``fetch``. It is OPTIONAL — if absent,
the defaults defined here apply and `slr` still works for read-only sources
that don't need an email.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictConfig(BaseModel):
    """Configuration must be explicit: reject keys the runtime does not use."""

    model_config = ConfigDict(extra="forbid")


class LLMStageConfig(StrictConfig):
    """How to reach the LLM for a given screening stage."""
    provider: Literal["anthropic", "openai", "openai-compatible"] | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    # full_text only: how the PDF is turned into model input.
    #   marker (default) — marker-pdf converts the PDF to markdown locally;
    #   pdf-inspector    — Firecrawl's layout-aware PDF converter runs locally;
    #   anydoc           — Firecrawl's AnyDoc converter runs locally;
    #   extend           — Extend Parse 2.0 API converts the PDF to markdown;
    #   datalab          — Datalab Marker API (hosted marker) converts the PDF;
    #   server           — your own self-hosted marker server (`slr marker-server`),
    #                      endpoint + key from MARKER_SERVER_URL / MARKER_SERVER_KEY;
    #   none             — send the raw PDF to the model (needs a PDF-capable
    #                      provider, e.g. Anthropic or OpenAI).
    # Conversion happens inline during `slr screen --stage full_text` — there is
    # no separate convert step. Overridable per run with `--converter`.
    converter: Literal[
        "marker", "pdf-inspector", "anydoc", "extend", "datalab", "server", "none"
    ] = "marker"


class ScreenConfig(StrictConfig):
    title_abstract: LLMStageConfig = Field(default_factory=LLMStageConfig)
    full_text: LLMStageConfig = Field(default_factory=LLMStageConfig)


class ProxyConfig(StrictConfig):
    """Optional institutional proxy; cookie_jar is Netscape/Mozilla cookies.txt."""
    enabled: bool = False
    url: str | None = None
    # Read only at fetch time; cookies are never persisted in review artifacts.
    cookie_jar: str | None = None

    @model_validator(mode="after")
    def is_actionable(self) -> "ProxyConfig":
        if self.enabled and not self.url:
            raise ValueError("fetch.proxy.url is required when the proxy is enabled")
        if not self.enabled and (self.url or self.cookie_jar):
            raise ValueError(
                "set fetch.proxy.enabled: true when configuring proxy.url or proxy.cookie_jar"
            )
        return self


class MarkerConfig(StrictConfig):
    use_llm: bool = False
    force_ocr: bool = False
    output_format: Literal["markdown", "json", "html"] = "markdown"
    # marker/surya leak native memory over long runs. Run it in a child process
    # that's recycled every `recycle_after` PDFs so RAM is reclaimed by process
    # exit. Set isolate=false to run in-process (no recycling).
    isolate: bool = True
    recycle_after: int = 50


class PdfInspectorConfig(StrictConfig):
    """Local Firecrawl pdf-inspector settings.

    Native text extraction is the default. Enabling OCR invokes pdf-inspector's
    selective local OCR path; it may require separately installed PDFium, ONNX
    Runtime, and OCR model artifacts.
    """

    use_ocr: bool = False
    model_directory: str | None = None
    offline: bool = False

    @model_validator(mode="after")
    def ocr_options_require_ocr(self) -> "PdfInspectorConfig":
        if not self.use_ocr and self.model_directory:
            raise ValueError(
                "fetch.pdf_inspector.model_directory requires use_ocr: true"
            )
        if not self.use_ocr and self.offline:
            raise ValueError("fetch.pdf_inspector.offline requires use_ocr: true")
        return self


class ExtendConfig(StrictConfig):
    """Extend Parse 2.0 API settings. The API key comes from EXTEND_API_KEY."""
    api_key_env: str = "EXTEND_API_KEY"
    api_version: str = "2026-02-09"
    target: Literal["markdown", "spatial"] = "markdown"
    timeout_s: int = 300
    poll_interval_s: float = 3.0


class DatalabConfig(StrictConfig):
    """Datalab Marker API settings. The API key comes from DATALAB_API_KEY."""
    api_key_env: str = "DATALAB_API_KEY"
    base_url: str = "https://www.datalab.to/api/v1"
    mode: Literal["fast", "balanced", "accurate"] = "balanced"
    max_pages: int | None = None
    timeout_s: int = 300
    poll_interval_s: float = 3.0


class ServerConfig(StrictConfig):
    """Self-hosted marker server (`slr marker-server`). Endpoint and key are read
    from environment variables so they never live in a shared review dir."""
    url_env: str = "MARKER_SERVER_URL"        # e.g. http://gpu-box:8000
    api_key_env: str = "MARKER_SERVER_KEY"    # optional bearer token
    output_format: Literal["markdown", "json", "html"] = "markdown"
    force_ocr: bool = False
    use_llm: bool = False
    timeout_s: int = 600                       # marker can be slow per PDF


class FetchConfig(StrictConfig):
    sources: list[str] = Field(
        default_factory=lambda: [
            "openalex_oa_url",
            "unpaywall",
            "europepmc_oa",
            "semantic_scholar",
            "core",
            "base",
            "openaire",
        ]
    )
    cache_dir: str = "pdfs/"
    markdown_dir: str = "markdown/"
    max_mb_per_pdf: int = 25
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    custom_resolvers: list[str] = Field(default_factory=list)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    pdf_inspector: PdfInspectorConfig = Field(default_factory=PdfInspectorConfig)
    extend: ExtendConfig = Field(default_factory=ExtendConfig)
    datalab: DatalabConfig = Field(default_factory=DatalabConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


class HttpConfig(StrictConfig):
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0


class SearchConfig(StrictConfig):
    """Settings for scholarly-source requests.

    ``contact_email`` identifies the caller to source APIs. The fetch command
    also passes it to OA resolvers such as Unpaywall, so one address is used
    consistently for every scholarly-source request.
    """

    contact_email: str | None = None
    http: HttpConfig = Field(default_factory=HttpConfig)


class Config(StrictConfig):
    """Active runtime settings, grouped by the `slr` command that uses them.

    Commands without durable runtime settings (init, import, dedup, export,
    prisma, and inspect) deliberately have no empty configuration section.
    Their reproducible methodological choices live in protocol.yaml or are
    explicit command options.
    """

    search: SearchConfig = Field(default_factory=SearchConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)


def load_config(path: str | Path | None) -> Config:
    """Load config.yaml. Missing file is fine — returns defaults."""
    if path is None:
        return Config()
    p = Path(path)
    if not p.exists():
        return Config()
    data = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    return Config.model_validate(data)


def load_config_for_review(review_dir: str | Path) -> Config:
    return load_config(Path(review_dir) / "config.yaml")


EXAMPLE_CONFIG = """\
# config.yaml — system / environment settings for slr.
# This file is OPTIONAL but you'll need it for anything that talks to an API
# (Unpaywall, the LLM screener, etc.). Treat it like .env — per-machine, not
# typically committed alongside review results.

# Search settings also identify you to OA resolvers used by `slr fetch`.
search:
  # Required by Unpaywall + good practice for the OpenAlex polite pool.
  contact_email: you@example.com
  # Retry policy for live source searches.
  http:
    max_retries: 5
    base_delay: 1.0
    max_delay: 60.0

# Per-stage LLM provider settings. Only meaningful when protocol.yaml has
# `screen.<stage>.type: llm`. Three provider shapes:
#   - anthropic         (uses ANTHROPIC_API_KEY)
#   - openai            (uses OPENAI_API_KEY)
#   - openai-compatible (uses base_url; key optional for local servers)
screen:
  title_abstract:
    provider: anthropic
    model: claude-sonnet-4-6
    # api_key_env: ANTHROPIC_API_KEY     # optional override
  full_text:
    provider: openai-compatible          # e.g. local Ollama / vLLM / llama.cpp
    base_url: http://localhost:11434/v1
    model: qwen2.5:32b
    # How each PDF becomes model input (conversion happens during screening —
    # there is no separate convert step). Override per run with `--converter`.
    #   marker (default) — marker-pdf converts the PDF to markdown locally.
    #   pdf-inspector    — Firecrawl's local, layout-aware PDF converter.
    #   anydoc           — Firecrawl's local document-to-Markdown converter.
    #   extend           — Extend Parse 2.0 API (needs EXTEND_API_KEY).
    #   datalab          — Datalab Marker API, hosted marker (needs DATALAB_API_KEY).
    #   server           — your own marker server (`slr marker-server`); set
    #                      MARKER_SERVER_URL (and MARKER_SERVER_KEY).
    #   none             — send the raw PDF to the model; requires a PDF-capable
    #                      provider, e.g. provider: anthropic, model: claude-sonnet-4-6.
    converter: marker

# Full-text PDF acquisition — order = resolver chain priority.
fetch:
  sources: [openalex_oa_url, unpaywall, europepmc_oa, semantic_scholar, core, base, openaire]
  cache_dir: pdfs/
  markdown_dir: markdown/
  max_mb_per_pdf: 25
  proxy:                                  # optional institutional EZproxy / OpenAthens
    enabled: false
    # When enabled, both of these are actionable (not inert placeholders):
    # url: https://login.ezproxy.your.uni/login?url={target}
    # cookie_jar: ~/.config/slr/cookies.txt # Netscape/Mozilla browser-exported format
  custom_resolvers: []                    # e.g. ['mylab.resolvers:figshare_mirror']
  marker:                                 # marker-pdf PDF -> Markdown (converter: marker)
    use_llm: false
    force_ocr: false                      # set true for scanned PDFs
    output_format: markdown               # markdown | json | html
    isolate: true                         # run marker in a recycled child process
    recycle_after: 50                     # restart that child every N PDFs (caps RAM)
  pdf_inspector:                          # Firecrawl local PDF -> Markdown (converter: pdf-inspector)
    use_ocr: false                        # selective local OCR for scanned/mixed PDFs
    # model_directory: /opt/models/pp-ocrv6-small
    # offline: true                       # require an already-warm OCR model cache
  extend:                                 # Extend Parse 2.0 (converter: extend)
    api_key_env: EXTEND_API_KEY           # where the API key is read from
    api_version: "2026-02-09"
    target: markdown                      # markdown | spatial
    timeout_s: 300                        # max wait per PDF parse
    poll_interval_s: 3.0
  datalab:                                # Datalab Marker API (converter: datalab)
    api_key_env: DATALAB_API_KEY
    mode: balanced                        # fast | balanced | accurate
    timeout_s: 300
    poll_interval_s: 3.0
  server:                                 # self-hosted marker server (converter: server)
    url_env: MARKER_SERVER_URL            # set this env var to e.g. http://gpu-box:8000
    api_key_env: MARKER_SERVER_KEY        # optional bearer token
    force_ocr: false
    timeout_s: 600

"""
