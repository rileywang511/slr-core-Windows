from __future__ import annotations

from pathlib import Path
from typing import Any

from slr.config import FetchConfig
from slr.fulltext.converters.base import ConverterEngine, register_converter
from slr.fulltext.extend import get_api_key


@register_converter("extend")
class ExtendConverter(ConverterEngine):
    def __init__(self, config: FetchConfig):
        super().__init__(config)
        get_api_key(config.extend)

    @property
    def engine_version(self) -> str:
        return f"extend-{self.config.extend.api_version}"

    @property
    def output_format(self) -> str:
        return "markdown"

    @property
    def config_payload(self) -> dict[str, Any]:
        return self.config.extend.model_dump()

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        from slr.fulltext.extend import run_extend
        return run_extend(pdf_path, self.config.extend)
