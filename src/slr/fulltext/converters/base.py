from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from slr.config import FetchConfig


class ConverterEngine(ABC):
    """Extension contract for a PDF-to-text engine.

    Engines validate their own configuration and convert one on-disk PDF. The
    shared controller owns cache decisions, output files, evidence rows, and
    lifecycle integration with screening.
    """

    name: str

    def __init__(self, config: FetchConfig):
        self.config = config

    @property
    @abstractmethod
    def engine_version(self) -> str: ...

    @property
    @abstractmethod
    def output_format(self) -> str: ...

    @property
    @abstractmethod
    def config_payload(self) -> dict[str, Any]: ...

    @property
    def used_llm(self) -> bool:
        return False

    @property
    def used_ocr(self) -> bool:
        return False

    @abstractmethod
    def convert(self, pdf_path: Path) -> tuple[str, int | None]: ...

    def close(self) -> None:
        """Release optional engine resources."""


_REGISTRY: dict[str, type[ConverterEngine]] = {}


def register_converter(name: str) -> Callable[[type[ConverterEngine]], type[ConverterEngine]]:
    """Register an engine without changing the shared conversion controller."""
    def decorator(cls: type[ConverterEngine]) -> type[ConverterEngine]:
        if name in _REGISTRY:
            raise ValueError(f"converter already registered: {name}")
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return decorator


def build_converter(name: str, config: FetchConfig) -> ConverterEngine:
    try:
        cls = _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"unknown converter: {name!r}; available: {sorted(_REGISTRY)}") from exc
    return cls(config)
