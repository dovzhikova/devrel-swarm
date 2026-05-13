"""Tests for agent config loader."""

from pathlib import Path

from devrel_origin.core.agent_config import AgentConfig, load_config


class TestLoadConfig:
    """Test loading agent_config.yaml."""

    def test_load_from_file(self, tmp_path):
        config_file = tmp_path / "agent_config.yaml"
        config_file.write_text("""
agents:
  sage:
    enabled: true
  iris:
    enabled: false
orchestration:
  workflow_order:
    - sage
    - iris
retry_settings:
  max_retries: 5
  initial_delay_seconds: 10
  backoff_multiplier: 3.0
  max_delay_seconds: 120
""")
        config = load_config(config_file)
        assert config.retry_settings["max_retries"] == 5
        assert config.workflow_order == ["sage", "iris"]
        assert config.agents["sage"]["enabled"] is True

    def test_load_default_when_missing(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.retry_settings["max_retries"] == 3
        assert len(config.workflow_order) == 4

    def test_real_config_file_loads(self):
        real_path = Path(__file__).parent.parent / "config" / "agent_config.yaml"
        if real_path.exists():
            config = load_config(real_path)
            assert "sage" in config.agents
            assert "kai" in config.agents


class TestAgentConfig:
    """Test AgentConfig defaults and per-agent config."""

    def test_defaults(self):
        config = AgentConfig()
        assert config.retry_settings["max_retries"] == 3
        assert config.retry_settings["backoff_multiplier"] == 2.0

    def test_get_agent_config_returns_defaults(self):
        config = AgentConfig()
        agent_cfg = config.get_agent_config("sage")
        assert agent_cfg["temperature"] == 0.7
        assert agent_cfg["model"] == "claude-sonnet-4-5-20250929"
        assert agent_cfg["max_tokens"] == 4096

    def test_get_agent_config_with_override(self):
        config = AgentConfig(agents={"kai": {"temperature": 0.9, "max_tokens": 8192}})
        kai_cfg = config.get_agent_config("kai")
        assert kai_cfg["temperature"] == 0.9
        assert kai_cfg["max_tokens"] == 8192
        assert kai_cfg["model"] == "claude-sonnet-4-5-20250929"

    def test_get_agent_config_unknown_agent(self):
        config = AgentConfig()
        cfg = config.get_agent_config("nonexistent")
        assert cfg["temperature"] == 0.7

    def test_load_config_merges_retry_defaults(self, tmp_path):
        yaml_content = "retry_settings:\n  max_retries: 10\n"
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_file)
        assert config.retry_settings["max_retries"] == 10
        assert config.retry_settings["initial_delay_seconds"] == 5
