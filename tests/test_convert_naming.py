"""FulltextConverter caching + naming. Markdown output mirrors the short
per-record PDF stem and includes the engine name: `<stem>.<engine>.<ext>`."""

from __future__ import annotations

import re
from pathlib import Path

import slr.fulltext.convert as convert_mod
from slr.config import FetchConfig, MarkerConfig
from slr.fulltext.convert import FulltextConverter
from tests import ReviewFiles


def _in_process_cfg() -> FetchConfig:
    # Run marker in-process so the stubbed _run_marker is exercised.
    return FetchConfig(marker=MarkerConfig(isolate=False))


def _make(tmp_path: Path, monkeypatch) -> tuple[FulltextConverter, ReviewFiles, Path]:
    review = tmp_path / "review"
    review.mkdir()
    store = ReviewFiles(review)
    store.init()
    pdfs = review / "pdfs"
    pdfs.mkdir()
    (pdfs / "abc1234567.pdf").write_bytes(b"%PDF-1.4 some bytes")
    monkeypatch.setattr(convert_mod, "_run_marker",
                        lambda *a, **k: ("# Converted markdown", 3))
    conv = FulltextConverter(review, "marker", _in_process_cfg())
    return conv, store, review


def test_markdown_filename_mirrors_short_pdf_stem(tmp_path, monkeypatch):
    conv, store, review = _make(tmp_path, monkeypatch)
    r = conv.convert_one(1, review / "pdfs" / "abc1234567.pdf")

    assert r.error is None
    assert r.output_path == "markdown/abc1234567.marker.md"
    assert (review / "markdown" / "abc1234567.marker.md").exists()
    names = [p.name for p in (review / "markdown").iterdir()]
    assert not any(re.fullmatch(r"[0-9a-f]{64}\..*", n) for n in names)
    assert re.fullmatch(r"[0-9a-f]{64}", r.pdf_sha256)
    assert r.converter == "marker"


def test_convert_text_returns_text_and_caches(tmp_path, monkeypatch):
    conv, store, review = _make(tmp_path, monkeypatch)
    pdf = review / "pdfs" / "abc1234567.pdf"

    text, err = conv.convert_text(1, pdf)
    assert err is None
    assert text == "# Converted markdown"

    # Second call is a cache hit — engine must not run again.
    monkeypatch.setattr(convert_mod, "_run_marker",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not re-run")))
    text2, err2 = conv.convert_text(1, pdf)
    assert err2 is None and text2 == "# Converted markdown"


def test_changed_pdf_bytes_invalidate_cache(tmp_path, monkeypatch):
    conv, _store, review = _make(tmp_path, monkeypatch)
    pdf = review / "pdfs" / "abc1234567.pdf"
    assert conv.convert_text(1, pdf)[0] == "# Converted markdown"

    pdf.write_bytes(b"%PDF-1.4 replacement bytes")
    monkeypatch.setattr(convert_mod, "_run_marker", lambda *a, **k: ("# Replacement", 1))
    assert conv.convert_text(1, pdf)[0] == "# Replacement"


def test_changed_converter_config_invalidates_cache(tmp_path, monkeypatch):
    conv, _store, review = _make(tmp_path, monkeypatch)
    pdf = review / "pdfs" / "abc1234567.pdf"
    assert conv.convert_text(1, pdf)[0] == "# Converted markdown"

    monkeypatch.setattr(convert_mod, "_run_marker", lambda *a, **k: ("# OCR configuration", 1))
    changed = FulltextConverter(
        review, "marker", FetchConfig(marker=MarkerConfig(isolate=False, force_ocr=True))
    )
    assert changed.convert_text(1, pdf)[0] == "# OCR configuration"


def test_converter_uses_configured_markdown_directory(tmp_path, monkeypatch):
    review = tmp_path / "review"
    review.mkdir()
    frames = ReviewFiles(review)
    frames.init()
    pdfs = review / "pdfs"
    pdfs.mkdir()
    pdf = pdfs / "paper--r1.pdf"
    pdf.write_bytes(b"%PDF-1.4 some bytes")
    monkeypatch.setattr(convert_mod, "_run_marker", lambda *a, **k: ("# Converted", 1))

    converter = FulltextConverter(
        review, "marker", FetchConfig(markdown_dir="converted/", marker=MarkerConfig(isolate=False)),
    )
    result = converter.convert_one(1, pdf)

    assert result.output_path == "converted/paper--r1.marker.md"
    assert (review / result.output_path).exists()


def test_convert_error_is_surfaced(tmp_path, monkeypatch):
    conv, store, review = _make(tmp_path, monkeypatch)
    monkeypatch.setattr(convert_mod, "_run_marker",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    text, err = conv.convert_text(2, review / "pdfs" / "abc1234567.pdf")
    assert text is None
    assert "boom" in err


def test_extend_and_marker_outputs_do_not_collide(tmp_path, monkeypatch):
    conv, store, review = _make(tmp_path, monkeypatch)
    pdf = review / "pdfs" / "abc1234567.pdf"
    conv.convert_one(1, pdf)
    # A second converter for the same PDF writes a distinct file.
    monkeypatch.setenv("EXTEND_API_KEY", "test-key")
    ext_conv = FulltextConverter(review, "extend", FetchConfig())
    monkeypatch.setattr(convert_mod, "run_extend", lambda *a, **k: ("extend text", 1), raising=False)
    import slr.fulltext.extend as extend_mod
    monkeypatch.setattr(extend_mod, "run_extend", lambda *a, **k: ("extend text", 1))
    r = ext_conv.convert_one(1, pdf)
    assert r.output_path == "markdown/abc1234567.extend.md"
    assert (review / "markdown" / "abc1234567.marker.md").exists()
    assert (review / "markdown" / "abc1234567.extend.md").exists()
