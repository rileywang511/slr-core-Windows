"""Local Firecrawl converter engines without installing their native wheels."""

from __future__ import annotations

import sys
import types
from pathlib import Path

from slr import frames
from slr.config import FetchConfig, PdfInspectorConfig
from slr.fulltext.convert import FulltextConverter
from tests import ReviewFiles


def _review_with_pdf(tmp_path: Path) -> tuple[Path, Path]:
    review = tmp_path / "review"
    review.mkdir()
    ReviewFiles(review).init()
    pdf = review / "pdfs" / "paper--r1.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4 test")
    return review, pdf


def test_pdf_inspector_converter_writes_markdown_and_page_evidence(tmp_path, monkeypatch):
    calls: dict[str, object] = {}
    module = types.ModuleType("pdf_inspector")

    def process_pdf(path: str):
        calls["path"] = path
        return types.SimpleNamespace(markdown="# Native PDF", page_count=8)

    module.process_pdf = process_pdf  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf_inspector", module)
    review, pdf = _review_with_pdf(tmp_path)

    result = FulltextConverter(review, "pdf-inspector", FetchConfig()).convert_one(1, pdf)

    assert calls["path"] == str(pdf)
    assert result.error is None
    assert result.pages == 8
    assert result.output_path == "markdown/paper--r1.pdf-inspector.md"
    assert (review / result.output_path).read_text() == "# Native PDF"
    assert frames.successful_conversions(review)[1]["converter"] == "pdf-inspector"


def test_pdf_inspector_selective_ocr_uses_configured_local_options(tmp_path, monkeypatch):
    calls: dict[str, object] = {}
    module = types.ModuleType("pdf_inspector")

    def process_pdf_with_ocr(path: str, **options):
        calls.update(path=path, **options)
        return types.SimpleNamespace(markdown="# OCR PDF", page_count=2)

    module.process_pdf_with_ocr = process_pdf_with_ocr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf_inspector", module)
    review, pdf = _review_with_pdf(tmp_path)
    cfg = FetchConfig(pdf_inspector=PdfInspectorConfig(
        use_ocr=True, model_directory="/models/ocr", offline=True,
    ))

    result = FulltextConverter(review, "pdf-inspector", cfg).convert_one(1, pdf)

    assert result.error is None
    assert calls == {"path": str(pdf), "model_directory": "/models/ocr", "offline": True}
    assert frames.successful_conversions(review)[1]["used_ocr"] == 1


def test_anydoc_converter_writes_local_markdown(tmp_path, monkeypatch):
    calls: list[str] = []
    module = types.ModuleType("anydoc")

    def to_markdown(path: str) -> str:
        calls.append(path)
        return "# AnyDoc PDF"

    module.to_markdown = to_markdown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anydoc", module)
    review, pdf = _review_with_pdf(tmp_path)

    result = FulltextConverter(review, "anydoc", FetchConfig()).convert_one(1, pdf)

    assert calls == [str(pdf)]
    assert result.error is None
    assert result.pages is None
    assert result.output_path == "markdown/paper--r1.anydoc.md"
    assert (review / result.output_path).read_text() == "# AnyDoc PDF"
