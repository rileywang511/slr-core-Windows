from __future__ import annotations

from pathlib import Path
from typing import Any

from slr.config import FetchConfig
from slr.fulltext.converters.base import ConverterEngine, register_converter


@register_converter("marker")
class MarkerConverter(ConverterEngine):
    def __init__(self, config: FetchConfig):
        super().__init__(config)
        from slr.fulltext.convert import _detect_marker_version

        self._version = _detect_marker_version()
        self._pool: Any = None
        if config.marker.isolate:
            from slr.fulltext.marker_worker import MarkerSubprocess
            self._pool = MarkerSubprocess(
                output_format=config.marker.output_format,
                use_llm=config.marker.use_llm,
                force_ocr=config.marker.force_ocr,
                recycle_after=config.marker.recycle_after,
            )

    @property
    def engine_version(self) -> str:
        return self._version

    @property
    def output_format(self) -> str:
        return self.config.marker.output_format

    @property
    def config_payload(self) -> dict[str, Any]:
        return self.config.marker.model_dump()

    @property
    def used_llm(self) -> bool:
        return self.config.marker.use_llm

    @property
    def used_ocr(self) -> bool:
        return self.config.marker.force_ocr

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        if self._pool is not None:
            return self._pool.convert(pdf_path)
        from slr.fulltext.convert import _run_marker

        return _run_marker(
            pdf_path,
            output_format=self.config.marker.output_format,
            use_llm=self.config.marker.use_llm,
            force_ocr=self.config.marker.force_ocr,
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
