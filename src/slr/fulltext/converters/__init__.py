from slr.fulltext.converters.base import ConverterEngine, build_converter
from slr.fulltext.converters import datalab as _datalab  # noqa: F401 - register built-ins
from slr.fulltext.converters import extend as _extend  # noqa: F401
from slr.fulltext.converters import firecrawl as _firecrawl  # noqa: F401
from slr.fulltext.converters import marker as _marker  # noqa: F401
from slr.fulltext.converters import server as _server  # noqa: F401

__all__ = ["ConverterEngine", "build_converter"]
