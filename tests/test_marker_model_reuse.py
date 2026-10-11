"""Regression: marker models must load ONCE per process and be reused across
PDFs — otherwise full-text screening reloads multi-GB weights per record and
memory balloons."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import slr.fulltext.convert as convert_mod


def _install_fake_marker(monkeypatch, calls: dict) -> None:
    models_mod = types.ModuleType("marker.models")

    def create_model_dict():
        calls["models"] += 1
        return {"weights": object()}

    models_mod.create_model_dict = create_model_dict

    pdf_mod = types.ModuleType("marker.converters.pdf")

    class PdfConverter:
        def __init__(self, artifact_dict, config):
            calls["converters"] += 1

        def __call__(self, path):
            calls["convert"] += 1
            return types.SimpleNamespace(metadata={"page_count": 2})

    pdf_mod.PdfConverter = PdfConverter

    output_mod = types.ModuleType("marker.output")
    output_mod.text_from_rendered = lambda rendered: ("body", None, None)

    for name, mod in {
        "marker": types.ModuleType("marker"),
        "marker.models": models_mod,
        "marker.converters": types.ModuleType("marker.converters"),
        "marker.converters.pdf": pdf_mod,
        "marker.output": output_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


def test_models_loaded_once_converter_built_once(monkeypatch):
    monkeypatch.setattr(convert_mod, "_MARKER_MODELS", None)
    monkeypatch.setattr(convert_mod, "_MARKER_CONVERTERS", {})
    monkeypatch.setattr(convert_mod, "_release_torch_memory", lambda: None)
    calls = {"models": 0, "converters": 0, "convert": 0}
    _install_fake_marker(monkeypatch, calls)

    t1 = convert_mod._run_marker(Path("a.pdf"))
    t2 = convert_mod._run_marker(Path("b.pdf"))
    t3 = convert_mod._run_marker(Path("c.pdf"))

    assert t1 == ("body", 2) == t2 == t3
    assert calls["models"] == 1       # multi-GB load happens once
    assert calls["converters"] == 1   # converter reused across PDFs
    assert calls["convert"] == 3       # all three PDFs still converted
