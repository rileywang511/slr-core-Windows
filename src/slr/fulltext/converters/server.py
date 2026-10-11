from __future__ import annotations

from pathlib import Path
from typing import Any

from slr.config import FetchConfig
from slr.fulltext.converters.base import ConverterEngine, register_converter
from slr.fulltext.marker_server import get_endpoint


@register_converter("server")
class MarkerServerConverter(ConverterEngine):
    def __init__(self, config: FetchConfig):
        super().__init__(config)
        get_endpoint(config.server)

    @property
    def engine_version(self) -> str:
        return "marker-server"

    @property
    def output_format(self) -> str:
        return self.config.server.output_format

    @property
    def config_payload(self) -> dict[str, Any]:
        return self.config.server.model_dump()

    def convert(self, pdf_path: Path) -> tuple[str, int | None]:
        from slr.fulltext.marker_server import convert_via_server
        return convert_via_server(pdf_path, self.config.server)
