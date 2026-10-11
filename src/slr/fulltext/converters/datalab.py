from __future__ import annotations

from pathlib import Path
from typing import Any

from slr.config import FetchConfig
from slr.fulltext.converters.base import ConverterEngine, register_converter
from slr.fulltext.datalab import get_api_key


@register_converter("datalab")
class DatalabConverter(ConverterEngine):
    def __init__(self, config: FetchConfig):
        super().__init__(config)
        get_api_key(config.datalab)

    @property
    def engine_version(self) -> str:
        return f"datalab-{self.config.datalab.mode}"

    @property
    def output_format(self) -> str:
        return "markdown"

    @property
    def config_payload(self) -> dict[str, Any]:
        return self.config.datalab.model_dump()

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        from slr.fulltext.datalab import run_datalab
        return run_datalab(pdf_path, self.config.datalab)
