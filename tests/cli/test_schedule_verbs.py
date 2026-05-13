"""Tests for `devrel schedule {install, list, remove}` subcommands."""

from __future__ import annotations

import os
from unittest.mock import patch

from typer.testing import CliRunner

from devrel_origin.cli import app

runner = CliRunner()


def _init(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        runner.invoke(
            app,
            ["init", "--non-interactive", "--name", "x", "--url", "", "--github-repo", ""],
        )
    finally:
        os.chdir(cwd)


def _run(tmp_path, args):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, args)
    finally:
        os.chdir(cwd)


def test_schedule_install_invokes_install_cron(tmp_path):
    _init(tmp_path)
    with patch("devrel_origin.cli.schedule.Scheduler") as M:
        inst = M.return_value
        inst.install_cron.return_value = ["* * * * * echo a", "0 9 * * 1 echo b"]
        r = _run(tmp_path, ["schedule", "install"])
    assert r.exit_code == 0, r.output
    inst.install_cron.assert_called_once()
    assert "Installed 2" in r.output


def test_schedule_list_shows_entries(tmp_path):
    _init(tmp_path)
    with patch("devrel_origin.cli.schedule.Scheduler") as M:
        inst = M.return_value
        inst.list_entries.return_value = [
            {
                "name": "weekly_cycle",
                "cron": "0 9 * * 1",
                "description": "Weekly run",
                "enabled": True,
                "command": "python -m devrel_origin.core.atlas --weekly-cycle",
            },
        ]
        r = _run(tmp_path, ["schedule", "list"])
    assert r.exit_code == 0, r.output
    inst.list_entries.assert_called_once()
    assert "weekly_cycle" in r.output
    assert "0 9 * * 1" in r.output


def test_schedule_remove_invokes_remove_cron(tmp_path):
    _init(tmp_path)
    with patch("devrel_origin.cli.schedule.Scheduler") as M:
        inst = M.return_value
        inst.remove_cron.return_value = None
        r = _run(tmp_path, ["schedule", "remove"])
    assert r.exit_code == 0, r.output
    inst.remove_cron.assert_called_once()
    assert "Removed" in r.output
