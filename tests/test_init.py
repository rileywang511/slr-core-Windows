from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from slr.cli import app


runner = CliRunner()


def test_init_creates_csv_review_artifacts_not_a_database(tmp_path: Path):
    review_dir = tmp_path / "review"

    result = runner.invoke(app, ["init", str(review_dir), "--json"])

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == str(review_dir / "_state.json")
    assert (review_dir / "protocol.yaml").is_file()
    assert (review_dir / "config.yaml").is_file()
    assert (review_dir / "_state.json").is_file()
    assert (review_dir / "audit.log.jsonl").is_file()
    assert not list(review_dir.glob("*.db"))
