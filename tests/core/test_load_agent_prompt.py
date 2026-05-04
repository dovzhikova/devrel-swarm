"""Tests for `load_agent_prompt` and `_OPTIMIZE_DIR` resolution.

Regression coverage for the bug where `_OPTIMIZE_DIR` resolved to
`src/devrel_swarm/optimize/` (which never exists), causing every caller
to silently fall through to its inline default — the maintainer's
prompt-customization workflow was broken without anybody noticing.
"""

from pathlib import Path

import pytest

from devrel_swarm.core import base


@pytest.fixture
def fake_optimize_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `base._OPTIMIZE_DIR` at a writable tmp dir for the test."""
    monkeypatch.setattr(base, "_OPTIMIZE_DIR", tmp_path)
    return tmp_path


class TestLoadAgentPrompt:
    def test_returns_inline_default_when_nothing_on_disk(self, fake_optimize_dir: Path):
        result = base.load_agent_prompt("kai", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "INLINE_DEFAULT"

    def test_loads_from_optimize_agent_subdir(self, fake_optimize_dir: Path):
        agent_dir = fake_optimize_dir / "kai"
        agent_dir.mkdir()
        (agent_dir / "system_prompt.txt").write_text("CUSTOM_KAI_PROMPT", encoding="utf-8")

        result = base.load_agent_prompt("kai", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "CUSTOM_KAI_PROMPT"

    def test_loads_from_optimize_agents_nested_layout(self, fake_optimize_dir: Path):
        # Current repo layout puts most agents under `optimize/agents/{name}/`
        # rather than `optimize/{name}/`. Both should resolve.
        agent_dir = fake_optimize_dir / "agents" / "echo"
        agent_dir.mkdir(parents=True)
        (agent_dir / "system_prompt.txt").write_text("ECHO_NESTED_PROMPT", encoding="utf-8")

        result = base.load_agent_prompt("echo", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "ECHO_NESTED_PROMPT"

    def test_appends_known_issues_when_present(self, fake_optimize_dir: Path):
        agent_dir = fake_optimize_dir / "kai"
        agent_dir.mkdir()
        (agent_dir / "system_prompt.txt").write_text("BASE", encoding="utf-8")
        (agent_dir / "known_issues.txt").write_text("ISSUE1\nISSUE2", encoding="utf-8")

        result = base.load_agent_prompt("kai", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "BASE\n\nISSUE1\nISSUE2"

    def test_known_issues_attached_to_nested_layout(self, fake_optimize_dir: Path):
        agent_dir = fake_optimize_dir / "agents" / "echo"
        agent_dir.mkdir(parents=True)
        (agent_dir / "system_prompt.txt").write_text("BASE", encoding="utf-8")
        (agent_dir / "known_issues.txt").write_text("NESTED_ISSUE", encoding="utf-8")

        result = base.load_agent_prompt("echo", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "BASE\n\nNESTED_ISSUE"

    def test_returns_default_when_optimize_dir_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # When _OPTIMIZE_DIR is None (installed-via-pip with no repo reachable),
        # callers must still get the inline default, not crash.
        monkeypatch.setattr(base, "_OPTIMIZE_DIR", None)
        result = base.load_agent_prompt("kai", "system_prompt.txt", "INLINE_DEFAULT")
        assert result == "INLINE_DEFAULT"


class TestResolveOptimizeDir:
    def test_resolves_to_repo_root_when_optimize_dir_present(self):
        # In the dev checkout (`~/devrel-swarm`), this test runs against the
        # real codebase, so `_OPTIMIZE_DIR` should resolve to a real path
        # that contains the on-disk `optimize/` tree.
        assert base._OPTIMIZE_DIR is not None
        assert (base._OPTIMIZE_DIR / "argus" / "system_prompt.txt").is_file()
        assert (base._OPTIMIZE_DIR / "agents" / "kai" / "system_prompt.txt").is_file()
