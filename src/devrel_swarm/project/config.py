"""Load .devrel/config.toml into a typed ProjectConfig.

The schema is intentionally narrow: project identity (required), model
selection (optional with sensible defaults), and budget guardrails
(optional). Future phases extend this with additional sections; the loader
is permissive about unknown top-level keys.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised when config.toml is malformed or missing required fields."""


@dataclass(frozen=True)
class ProjectIdentity:
    name: str
    url: str = ""
    github_repo: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    default: str = "claude-sonnet-4-6"
    cheap: str = "claude-haiku-4-5-20251001"
    opus_opt_in: bool = True


@dataclass(frozen=True)
class BudgetConfig:
    monthly_usd: float = 100.0
    warn_at_pct: int = 80


@dataclass(frozen=True)
class ProjectConfig:
    project: ProjectIdentity
    model: ModelConfig = field(default_factory=ModelConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    @classmethod
    def load(cls, config_file: Path) -> "ProjectConfig":
        if not config_file.is_file():
            raise ConfigError(f"config.toml not found at {config_file}")
        with config_file.open("rb") as f:
            raw = tomllib.load(f)
        if "project" not in raw:
            raise ConfigError("config.toml missing required [project] section")
        proj = raw["project"]
        if "name" not in proj or not proj["name"]:
            raise ConfigError("config.toml missing required project.name")
        identity = ProjectIdentity(
            name=str(proj["name"]),
            url=str(proj.get("url", "")),
            github_repo=proj.get("github_repo"),
        )
        model_raw = raw.get("model") or {}
        defaults = ModelConfig()
        model = ModelConfig(
            default=str(model_raw.get("default", defaults.default)),
            cheap=str(model_raw.get("cheap", defaults.cheap)),
            opus_opt_in=bool(model_raw.get("opus_opt_in", defaults.opus_opt_in)),
        )
        budget_raw = raw.get("budget") or {}
        bdefaults = BudgetConfig()
        budget = BudgetConfig(
            monthly_usd=float(budget_raw.get("monthly_usd", bdefaults.monthly_usd)),
            warn_at_pct=int(budget_raw.get("warn_at_pct", bdefaults.warn_at_pct)),
        )
        return cls(project=identity, model=model, budget=budget)
