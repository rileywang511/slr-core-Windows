"""Native-platform regressions: real process locks, UTF-8 and npm shims."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from slr import frames, prisma
from slr.audit import AuditLog
from slr.cli import app
from slr.config import EXAMPLE_CONFIG, load_config_for_review
from slr.exports.csv import export_csv
from slr.exports.jsonl import export_jsonl
from slr.exports.ris import export_ris
from slr.protocol import EXAMPLE_PROTOCOL, load_protocol

_TRY_LOCK = """
import sys
from pathlib import Path
from slr.frames import review_write_lock
try:
    with review_write_lock(Path(sys.argv[1])):
        pass
except RuntimeError as exc:
    if "already writing" not in str(exc):
        raise
    sys.exit(23)
"""


def test_lock_excludes_other_process_and_releases_on_error(tmp_path):
    review = tmp_path / "中文 review & spaces"

    def try_lock():
        return subprocess.run(
            [sys.executable, "-c", _TRY_LOCK, str(review)],
            capture_output=True, timeout=15, check=False,
        ).returncode

    with pytest.raises(ValueError):
        with frames.review_write_lock(review):
            assert try_lock() == 23
            raise ValueError("command failed")
    assert try_lock() == 0
    # Existing dotfiles must never be treated as stale held locks.
    assert try_lock() == 0


def test_utf8_artifacts_and_bom_configuration(tmp_path):
    review = tmp_path / "中文 review"
    frames.init(review)
    title = "中文研究 – café 🧪"
    # Keep the example schema and replace its title through parsed YAML.
    import yaml

    payload = yaml.safe_load(EXAMPLE_PROTOCOL)
    payload["title"] = title
    (review / "protocol.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8-sig"
    )
    (review / "config.yaml").write_text(EXAMPLE_CONFIG, encoding="utf-8-sig")
    assert load_protocol(review / "protocol.yaml").title == title
    assert load_config_for_review(review) is not None
    frames._write(review / frames.RECORDS, frames.RECORD_COLS, [{"id": 1, "title": title}])
    assert frames._read(review / frames.RECORDS)[0]["title"] == title
    frames.atomic_write_text(review / "markdown.md", title)
    assert (review / "markdown.md").read_bytes() == title.encode("utf-8")
    audit = AuditLog(review)
    audit.append("test", details={"title": title})
    assert audit.read()[0]["details"]["title"] == title
    for exporter, suffix in [(export_csv, "csv"), (export_jsonl, "jsonl"), (export_ris, "ris")]:
        output = review / f"export.{suffix}"
        assert exporter(review, output) == 1
        text = output.read_text(encoding="utf-8")
        if suffix == "jsonl":
            assert json.loads(text)["title"] == title
        else:
            assert title in text


def test_lock_released_after_process_crash(tmp_path):
    code = """
import os, sys
from pathlib import Path
from slr.frames import review_write_lock
with review_write_lock(Path(sys.argv[1])):
    os._exit(7)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 7
    with frames.review_write_lock(tmp_path):
        pass


def test_cli_workflow_with_unicode_and_spaces(tmp_path):
    review = tmp_path / "中文 review & spaces"
    imported = tmp_path / "中文 input.csv"
    imported.write_text("title,year\n中文研究 café,2024\n", encoding="utf-8-sig")
    runner = CliRunner()
    for command in [
        ["init", str(review)],
        ["import", str(review), str(imported)],
        ["dedup", str(review)],
        ["prisma", str(review)],
        ["export", str(review), "--format", "ris"],
    ]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output, result.exception)
    assert "中文研究 café" in (review / frames.RECORDS).read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows npm shim layout")
@pytest.mark.parametrize("local", [False, True])
def test_mermaid_windows_npm_shim(tmp_path, monkeypatch, local):
    root = tmp_path / "node tools"
    shim = (root / "node_modules" / ".bin" if local else root) / "mmdc.cmd"
    entry = root / "node_modules" / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(prisma.shutil, "which", lambda name: str(shim) if name == "mmdc" else "node.exe")
    assert prisma._mermaid_command() == ["node.exe", str(entry)]


def test_prisma_keeps_json_and_mermaid_if_renderer_cannot_start(tmp_path, monkeypatch):
    frames.init(tmp_path)
    monkeypatch.setattr(prisma, "_mermaid_command", lambda: ["missing-mmdc"])

    def fail(*args, **kwargs):
        raise OSError("cannot execute renderer")

    monkeypatch.setattr(prisma.subprocess, "run", fail)
    outputs = prisma.write_outputs(tmp_path)
    assert Path(outputs["json"]).is_file()
    assert Path(outputs["mmd"]).is_file()
    assert outputs["svg"] == ""
