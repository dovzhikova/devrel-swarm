"""Agent configuration loader from YAML."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_WORKFLOW_ORDER = ["sage", "iris", "nova", "kai"]
DEFAULT_AGENT_CONFIG: dict[str, Any] = {
    "temperature": 0.7,
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 4096,
}
DEFAULT_RETRY = {
    "max_retries": 3,
    "initial_delay_seconds": 5,
    "backoff_multiplier": 2.0,
    "max_delay_seconds": 60,
}


@dataclass
class AgentConfig:
    """Parsed agent configuration."""

    product_name: str = os.getenv("PRODUCT_NAME", "OpenClaw")
    product_url: str = os.getenv("PRODUCT_URL", "https://openclaw.ai")
    budget_limit_usd: float = 10.0  # Weekly budget; 0 = unlimited
    agents: dict[str, Any] = field(default_factory=dict)
    workflow_order: list[str] = field(default_factory=lambda: list(DEFAULT_WORKFLOW_ORDER))
    retry_settings: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RETRY))
    api_clients: dict[str, Any] = field(default_factory=dict)
    logging_config: dict[str, Any] = field(default_factory=dict)

    def get_agent_config(self, agent_name: str) -> dict[str, Any]:
        """Get config for a specific agent, merging with defaults."""
        defaults = dict(DEFAULT_AGENT_CONFIG)
        overrides = self.agents.get(agent_name, {})
        return {**defaults, **overrides}


def load_config(path: Path) -> AgentConfig:
    """Load config from YAML file, falling back to defaults."""
    if not path.exists():
        logger.warning(f"Config not found at {path}, using defaults")
        return AgentConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    return AgentConfig(
        product_name=raw.get("product_name", os.getenv("PRODUCT_NAME", "OpenClaw")),
        product_url=raw.get("product_url", os.getenv("PRODUCT_URL", "https://openclaw.ai")),
        budget_limit_usd=raw.get("budget_limit_usd", 10.0),
        agents=raw.get("agents", {}),
        workflow_order=raw.get("orchestration", {}).get("workflow_order", DEFAULT_WORKFLOW_ORDER),
        retry_settings={**DEFAULT_RETRY, **raw.get("retry_settings", {})},
        api_clients=raw.get("api_clients", {}),
        logging_config=raw.get("logging", {}),
    )
