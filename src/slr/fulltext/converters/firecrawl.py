"""Local Firecrawl PDF-to-Markdown converter engines.

Both engines call the projects' Python bindings in-process: no document bytes
leave the review machine. pdf-inspector exposes its page count and can opt into
selective local OCR; AnyDoc uses pdf-inspector internally for native-text PDFs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from slr.fulltext.converters.base import ConverterEngine, register_converter


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


@register_converter("pdf-inspector")
class PdfInspectorConverter(ConverterEngine):
    """Convert PDFs locally with Firecrawl's layout-aware pdf-inspector."""

    @property
    def engine_version(self) -> str:
        return f"pdf-inspector-{_package_version('pdf-inspector')}"

    @property
    def output_format(self) -> str:
        return "markdown"

    @property
    def config_payload(self) -> dict[str, Any]:
        return self.config.pdf_inspector.model_dump()

    @property
    def used_ocr(self) -> bool:
        return self.config.pdf_inspector.use_ocr

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        try:
            import pdf_inspector
        except ImportError as exc:
            raise ImportError(
                "pdf-inspector is not installed; install with "
                "`uv pip install -e '.[pdf-inspector]'`"
            ) from exc

        options: dict[str, Any] = {}
        if self.config.pdf_inspector.model_directory:
            options["model_directory"] = self.config.pdf_inspector.model_directory
        if self.config.pdf_inspector.offline:
            options["offline"] = True
        result = (
            pdf_inspector.process_pdf_with_ocr(str(pdf_path), **options)
            if self.config.pdf_inspector.use_ocr
            else pdf_inspector.process_pdf(str(pdf_path))
        )
        markdown = getattr(result, "markdown", None)
        if not markdown:
            pdf_type = getattr(result, "pdf_type", "unknown")
            raise RuntimeError(
                f"pdf-inspector produced no Markdown for {pdf_type!r} PDF; "
                "set fetch.pdf_inspector.use_ocr: true for selective local OCR"
            )
        return str(markdown), getattr(result, "page_count", None)


@register_converter("anydoc")
class AnyDocConverter(ConverterEngine):
    """Convert PDFs locally with Firecrawl's AnyDoc Markdown serializer."""

    @property
    def engine_version(self) -> str:
        return f"anydoc-{_package_version('firecrawl-anydoc')}"

    @property
    def output_format(self) -> str:
        return "markdown"

    @property
    def config_payload(self) -> dict[str, Any]:
        return {}

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        try:
            import anydoc
        except ImportError as exc:
            raise ImportError(
                "firecrawl-anydoc is not installed; install with "
                "`uv pip install -e '.[anydoc]'`"
            ) from exc

        markdown = anydoc.to_markdown(str(pdf_path))
        if not markdown:
            raise RuntimeError("AnyDoc produced no Markdown")
        # AnyDoc's Python binding returns Markdown only, without page metadata.
        return str(markdown), None
