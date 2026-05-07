"""CLI smoke tests for `devrel cro ...`."""

from typer.testing import CliRunner

from devrel_swarm.cli import app


def test_cro_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "--help"])
    assert result.exit_code == 0
    # Only `report` lands in this task; other verbs come in Tasks 11-13.
    assert "report" in result.output.lower()


def test_cro_report_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "report", "--help"])
    assert result.exit_code == 0
    assert "since" in result.output.lower()
