from typer.testing import CliRunner

from slr.cli import app


def test_replay_scaffold_is_not_a_cli_command():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "replay" not in result.stdout.lower()
