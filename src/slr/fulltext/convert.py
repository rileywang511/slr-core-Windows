"""PDF -> text conversion for full-text screening.

Conversion engines are registered in ``slr.fulltext.converters``. The built-in
engines are marker-pdf (local), Extend Parse, Datalab Marker, and a self-hosted
marker server; new engines use the documented ``ConverterEngine`` contract.

Conversion runs inline during `slr screen --stage full_text` (there is no
separate convert step). Output is cached at `markdown/<pdf-stem>.<engine>.<ext>`
and recorded in `fulltext_conversions`, so re-runs and resumes are free.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slr.audit import sha256_file, utc_now_iso
from slr.config import FetchConfig
from slr.fulltext.converters import ConverterEngine, build_converter
from slr import frames


@dataclass
class ConversionResult:
    record_id: int
    converter: str
    pdf_sha256: str
    engine_version: str
    config_hash: str
    output_format: str
    output_path: str | None
    pages: int | None
    elapsed_ms: int
    converted_at: str
    error: str | None


class FulltextConverter:
    """Converts PDFs to text with a selectable engine, caching the output."""

    def __init__(
        self,
        review_dir: Path,
        converter: str,
        config: FetchConfig,
    ):
        self.review_dir = Path(review_dir)
        self.config = config
        self.engine: ConverterEngine = build_converter(converter, config)
        self.converter = self.engine.name
        self.markdown_dir = self.review_dir / config.markdown_dir
        self.markdown_dir.mkdir(parents=True, exist_ok=True)
        self.engine_version = self.engine.engine_version
        self._config_hash = hashlib.sha256(
            json.dumps({"converter": self.converter, **self.engine.config_payload}, sort_keys=True).encode()
        ).hexdigest()[:16]

    # ---- public API ----

    def convert_text(self, record_id: int, pdf_path: Path) -> tuple[str | None, str | None]:
        """Return (converted_text, error). Exactly one is non-None."""
        result = self.convert_one(record_id, Path(pdf_path))
        if result.error:
            return None, result.error
        if not result.output_path:
            return None, "no output produced"
        try:
            return (self.review_dir / result.output_path).read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, str(exc)

    def convert_one(self, record_id: int, pdf_path: Path) -> ConversionResult:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            return self._error_result(record_id, "pdf missing", "")
        out_path = self.markdown_dir / f"{pdf_path.stem}.{self.converter}.{self._extension()}"
        pdf_sha = sha256_file(pdf_path) or ""
        cached = frames.matching_conversion(
            self.review_dir, record_id, self.converter, pdf_sha, self._config_hash
        )
        if cached and (self.review_dir / cached["output_path"]).exists():
            # A cache hit requires both matching persisted evidence and its file.
            return ConversionResult(
                record_id=record_id, converter=self.converter, pdf_sha256=pdf_sha,
                engine_version=self.engine_version, config_hash=self._config_hash,
                output_format=self._output_format(),
                output_path=cached["output_path"], pages=cached.get("pages"),
                elapsed_ms=0, converted_at=utc_now_iso(), error=None,
            )

        t0 = time.time()
        try:
            text, pages = self._run_engine(pdf_path)
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000)
            return self._error_result(record_id, str(exc), pdf_sha, elapsed_ms=elapsed)

        frames.atomic_write_text(out_path, text)
        result = ConversionResult(
            record_id=record_id, converter=self.converter, pdf_sha256=pdf_sha,
            engine_version=self.engine_version, config_hash=self._config_hash,
            output_format=self._output_format(),
            output_path=out_path.relative_to(self.review_dir).as_posix(),
            pages=pages, elapsed_ms=int((time.time() - t0) * 1000),
            converted_at=utc_now_iso(), error=None,
        )
        self._persist(result)
        return result

    # ---- internal ----

    def _run_engine(self, pdf_path: Path) -> tuple[str, int | None]:
        return self.engine.convert(pdf_path)

    def close(self) -> None:
        """Release resources held by the selected engine, if any."""
        self.engine.close()

    def _output_format(self) -> str:
        return self.engine.output_format

    def _extension(self) -> str:
        return {"markdown": "md", "json": "json", "html": "html"}.get(self._output_format(), "txt")

    def _error_result(
        self, record_id: int, error: str, pdf_sha: str, elapsed_ms: int = 0
    ) -> ConversionResult:
        result = ConversionResult(
            record_id=record_id, converter=self.converter, pdf_sha256=pdf_sha,
            engine_version=self.engine_version, config_hash=self._config_hash,
            output_format=self._output_format(), output_path=None, pages=None,
            elapsed_ms=elapsed_ms, converted_at=utc_now_iso(), error=error,
        )
        self._persist(result)
        return result

    def _persist(self, r: ConversionResult) -> None:
        frames.append_conversion(self.review_dir, {
            "record_id": r.record_id,
            "converter": r.converter,
            "pdf_sha256": r.pdf_sha256,
            "marker_version": r.engine_version,
            "config_hash": r.config_hash,
            "output_format": r.output_format,
            "output_path": r.output_path,
            "pages": r.pages,
            "used_llm": 1 if self.engine.used_llm else 0,
            "used_ocr": 1 if self.engine.used_ocr else 0,
            "elapsed_ms": r.elapsed_ms,
            "converted_at": r.converted_at,
            "error": r.error,
        })


def _detect_marker_version() -> str:
    try:
        import marker  # type: ignore
        return getattr(marker, "__version__", "unknown")
    except ImportError:
        return "not-installed"


# marker's models are several GB of torch weights. Load them ONCE per process
# and reuse the converter for every PDF — otherwise screening reloads the whole
# model stack per record and RAM balloons. Keyed by config so distinct settings
# (e.g. force_ocr) get their own converter while still sharing the loaded models.
_MARKER_MODELS: Any = None
_MARKER_CONVERTERS: dict[tuple[str, bool, bool], Any] = {}


def _marker_converter(output_format: str, use_llm: bool, force_ocr: bool) -> Any:
    global _MARKER_MODELS
    key = (output_format, use_llm, force_ocr)
    converter = _MARKER_CONVERTERS.get(key)
    if converter is not None:
        return converter
    try:
        from marker.converters.pdf import PdfConverter  # type: ignore
        from marker.models import create_model_dict  # type: ignore
    except ImportError as e:
        raise ImportError(
            "marker-pdf not installed; install with `pip install 'slr-agent[marker]'`"
        ) from e
    if _MARKER_MODELS is None:
        _MARKER_MODELS = create_model_dict()  # the expensive, multi-GB load — once
    config: dict[str, Any] = {"output_format": output_format}
    if force_ocr:
        config["force_ocr"] = True
    if use_llm:
        config["use_llm"] = True
    converter = PdfConverter(artifact_dict=_MARKER_MODELS, config=config)
    _MARKER_CONVERTERS[key] = converter
    return converter


def _release_torch_memory() -> None:
    """Best-effort: drop per-PDF tensors so RAM doesn't creep up over a long run."""
    import gc
    gc.collect()
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif getattr(getattr(torch.backends, "mps", None), "is_available", lambda: False)():
            torch.mps.empty_cache()
    except Exception:
        pass


def _run_marker(
    pdf_path: Path,
    output_format: str = "markdown",
    use_llm: bool = False,
    force_ocr: bool = False,
) -> tuple[str, int | None]:
    """Run marker-pdf on a PDF (reusing cached models). Returns (text, n_pages)."""
    try:
        from marker.output import text_from_rendered  # type: ignore
    except ImportError as e:
        raise ImportError(
            "marker-pdf not installed; install with `pip install 'slr-agent[marker]'`"
        ) from e

    converter = _marker_converter(output_format, use_llm, force_ocr)
    rendered = converter(str(pdf_path))
    text, _meta, _images = text_from_rendered(rendered)
    pages = getattr(rendered, "metadata", {}).get("page_count") if hasattr(rendered, "metadata") else None
    del rendered
    _release_torch_memory()
    return text, pages
