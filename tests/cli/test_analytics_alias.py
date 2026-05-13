"""Verify `devrel analytics ...` still works as an alias for `devrel argus ...`."""

import warnings

from typer.testing import CliRunner

from devrel_origin.cli import app


def test_analytics_subcommand_runs_with_deprecation_warning():
    runner = CliRunner()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = runner.invoke(app, ["analytics", "--help"])
    assert result.exit_code == 0
    assert "argus" in result.output.lower() or "deprecated" in result.output.lower()


def test_argus_subcommand_runs_directly():
    runner = CliRunner()
    result = runner.invoke(app, ["argus", "--help"])
    assert result.exit_code == 0
    assert "report" in result.output.lower()
