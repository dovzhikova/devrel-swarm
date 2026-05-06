"""Smoke tests for the cross-pillar `devrel growth` umbrella."""

from typer.testing import CliRunner

from devrel_swarm.cli import app


class TestGrowthUmbrella:
    def test_growth_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "--help"])
        assert result.exit_code == 0
        assert "summary" in result.output.lower()
        assert "diff" in result.output.lower()

    def test_growth_summary_runs(self, tmp_path, monkeypatch):
        """Placeholder summary verb should exit 0 even with no data."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".devrel").mkdir()
        (tmp_path / ".devrel" / "config.toml").write_text(
            'product_name = "Test"\nproduct_url = "https://example.com"\n'
        )
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "summary"])
        assert result.exit_code == 0

    def test_growth_diff_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".devrel").mkdir()
        (tmp_path / ".devrel" / "config.toml").write_text(
            'product_name = "Test"\nproduct_url = "https://example.com"\n'
        )
        runner = CliRunner()
        result = runner.invoke(app, ["growth", "diff", "2026-04-01", "2026-04-08"])
        assert result.exit_code == 0
