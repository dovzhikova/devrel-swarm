"""CLI smoke tests for `devrel cro ...`."""

import sqlite3

from typer.testing import CliRunner

from devrel_origin.cli import app


def test_cro_help_lists_subcommands():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "--help"])
    assert result.exit_code == 0
    for verb in ("report", "history", "diff", "calibration", "funnel"):
        assert verb in result.output.lower()


def test_cro_report_help_runs():
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "report", "--help"])
    assert result.exit_code == 0
    assert "since" in result.output.lower()


def test_cro_report_inserts_analytics_report_row(tmp_path, monkeypatch):
    """Regression: report verb must seed analytics_reports row to satisfy FK."""
    from devrel_origin.cli import cro as cro_module
    from devrel_origin.project import state

    monkeypatch.chdir(tmp_path)
    devrel_dir = tmp_path / ".devrel"
    devrel_dir.mkdir()
    (devrel_dir / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    db_path = devrel_dir / "state.db"
    state.init_db(db_path)

    # Capture the report_id passed to execute so we can verify the FK row exists.
    captured: dict = {}

    class _FakeReport:
        period_end = "2026-05-07"
        funnel_id = "default"
        dropoffs = []
        recommendations = []

    async def _fake_execute(self, **kwargs):
        captured["report_id"] = kwargs.get("report_id")
        return _FakeReport()

    monkeypatch.setattr(cro_module.Cyra, "execute", _fake_execute)

    # Stub _build_cyra so no env vars are required.
    original_build_cyra = cro_module._build_cyra

    def _fake_build_cyra(db_path_arg):
        # Instantiate a Cyra shell without live clients by bypassing __init__.
        obj = object.__new__(cro_module.Cyra)
        obj.db_path = db_path_arg  # type: ignore[attr-defined]
        return obj

    monkeypatch.setattr(cro_module, "_build_cyra", _fake_build_cyra)

    runner = CliRunner()
    result = runner.invoke(app, ["cro", "report"])
    assert result.exit_code == 0, f"CLI failed: {result.output!r}"

    # The execute call must have received an integer report_id.
    assert isinstance(captured.get("report_id"), int), (
        f"execute() got report_id={captured.get('report_id')!r}; expected an int"
    )

    # That integer must exist as a real row in analytics_reports (FK anchor).
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM analytics_reports WHERE id = ?", (captured["report_id"],)
        ).fetchone()
    assert row is not None, (
        f"report_id={captured['report_id']} not found in analytics_reports; "
        "the CLI must seed this row before calling execute to avoid FK violation"
    )

    # Exactly one report row should exist for this run.
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM analytics_reports").fetchone()[0]
    assert count == 1, f"Expected 1 analytics_reports row, got {count}"


def test_cro_history_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "history", "signup_started"])
    assert result.exit_code == 0


def test_cro_diff_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "diff", "2026-04-01", "2026-04-08"])
    assert result.exit_code == 0


def test_cro_calibration_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["cro", "calibration"])
    assert result.exit_code == 0


def test_cro_funnel_inspector_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".devrel").mkdir()
    (tmp_path / ".devrel" / "config.toml").write_text(
        'product_name = "Test"\nproduct_url = "https://example.com"\n'
    )
    # The funnel verb requires PostHog + LLM env vars. Stub them with no-op classes.
    import devrel_origin.cli.cro as cro_module

    monkeypatch.setattr(cro_module, "PostHogClient", lambda **kw: object())

    class _StubLLM:
        def set_agent(self, _):
            pass

    monkeypatch.setattr(cro_module, "LLMClient", lambda **kw: _StubLLM())

    # Stub Cyra._autodetect_funnel to return a placeholder list without calling PostHog.
    async def _fake_autodetect(self, days=7):
        return ["$pageview", "signup_started"]

    monkeypatch.setattr(cro_module.Cyra, "_autodetect_funnel", _fake_autodetect)

    runner = CliRunner()
    result = runner.invoke(app, ["cro", "funnel", "--show-detected"])
    assert result.exit_code == 0
