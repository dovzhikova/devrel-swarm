"""
Watchdog — System Health Monitor Agent

Monitors agent pipeline health: checks for stale outputs, failed runs,
token budget consumption, and integration connectivity. Produces a
health report with actionable alerts.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from devrel_swarm.core.llm import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class AgentHealthCheck:
    """Health status for a single agent."""

    agent: str
    status: str  # "healthy", "stale", "failed", "unknown"
    last_run: str
    output_age_hours: float
    issues: list[str] = field(default_factory=list)


@dataclass
class SystemHealthReport:
    """Full system health report."""

    timestamp: str
    overall_score: int  # 0-100
    agents: list[AgentHealthCheck]
    budget_usage: dict[str, Any]
    integration_status: dict[str, str]
    alerts: list[str]
    recommendations: list[str]


class Watchdog:
    """
    System health monitoring agent.

    Capabilities:
    - Check agent output freshness (stale detection)
    - Monitor token budget consumption per agent
    - Verify integration connectivity (APIs, search tools)
    - Produce health scores and actionable alerts
    - Track week-over-week health trends
    """

    STALE_THRESHOLD_HOURS = 168  # 7 days

    def __init__(
        self,
        archive_dir: Path = Path("context_archive"),
        llm_client: Optional[LLMClient] = None,
    ):
        self.archive_dir = archive_dir
        self.llm_client = llm_client

    async def execute(
        self,
        task: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run system health check."""
        logger.info(f"Watchdog executing: {task[:80]}...")

        agent_checks = self._check_agent_health(context)
        budget = self._check_budget(context)
        integrations = await self._check_integrations()
        alerts = self._generate_alerts(agent_checks, budget, integrations)
        score = self._compute_health_score(agent_checks, integrations)

        return {
            "agent": "watchdog",
            "task": task,
            "timestamp": datetime.now().isoformat(),
            "overall_score": score,
            "agent_health": [
                {
                    "agent": c.agent,
                    "status": c.status,
                    "last_run": c.last_run,
                    "issues": c.issues,
                }
                for c in agent_checks
            ],
            "budget_usage": budget,
            "integration_status": integrations,
            "alerts": alerts,
            "status": "checked",
        }

    def _check_agent_health(
        self, context: dict[str, Any] | None,
    ) -> list[AgentHealthCheck]:
        """Check each agent's output freshness from context."""
        checks = []
        agent_fields = {
            "sage": "sage_triage",
            "echo": "echo_social",
            "iris": "iris_themes",
            "nova": "nova_experiments",
            "kai": "kai_content",
            "vox": "vox_video",
            "dex": "dex_docs",
            "rex": "rex_competitive",
            "pax": "pax_sales",
            "mox": "mox_campaigns",
        }

        for agent, field_name in agent_fields.items():
            data = (context or {}).get(field_name, {})
            if isinstance(data, dict) and data:
                checks.append(AgentHealthCheck(
                    agent=agent,
                    status="healthy",
                    last_run=data.get("timestamp", "unknown"),
                    output_age_hours=0,
                ))
            else:
                checks.append(AgentHealthCheck(
                    agent=agent,
                    status="stale" if context else "unknown",
                    last_run="never",
                    output_age_hours=999,
                    issues=[f"{agent} has no output in current context"],
                ))

        return checks

    def _check_budget(self, context: dict[str, Any] | None) -> dict[str, Any]:
        """Check token budget consumption and cost."""
        if not self.llm_client:
            return {"status": "no_client", "per_agent": {}}

        usage = self.llm_client.usage
        budget_limit = getattr(self.llm_client, "budget_limit_usd", 0)
        return {
            "total_input_tokens": usage.total_input_tokens,
            "total_output_tokens": usage.total_output_tokens,
            "total_calls": usage.total_calls,
            "total_cost_usd": round(usage.total_cost_usd, 4),
            "budget_limit_usd": budget_limit,
            "budget_remaining_usd": round(max(0, budget_limit - usage.total_cost_usd), 4),
            "per_agent": dict(usage.per_agent),
            "status": "tracked",
        }

    async def _check_integrations(self) -> dict[str, str]:
        """Probe actual integration endpoints for connectivity."""
        probes: dict[str, tuple[str, dict[str, str]]] = {}

        github_token = os.environ.get("GITHUB_TOKEN", "")
        if github_token:
            probes["github"] = (
                "https://api.github.com",
                {"Authorization": f"Bearer {github_token}"},
            )

        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            probes["llm"] = (
                "https://api.anthropic.com/v1/models",
                {
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
            )

        firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "")
        if firecrawl_key:
            # Use a GET-able endpoint; /v1/scrape is POST-only and always
            # returns 405 on GET, masking healthy auth as unhealthy.
            probes["search"] = (
                "https://api.firecrawl.dev/v1/team",
                {"Authorization": f"Bearer {firecrawl_key}"},
            )

        instantly_key = os.environ.get("INSTANTLY_API_KEY", "")
        if instantly_key:
            probes["instantly"] = (
                "https://api.instantly.ai/api/v2/campaigns?limit=1",
                {"Authorization": f"Bearer {instantly_key}"},
            )

        status: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            async def _probe(name: str, url: str, headers: dict) -> tuple[str, str]:
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code < 400:
                        return name, "connected"
                    return name, f"error_{resp.status_code}"
                except Exception as exc:
                    return name, f"unreachable: {type(exc).__name__}"

            results = await asyncio.gather(*[
                _probe(name, url, hdrs)
                for name, (url, hdrs) in probes.items()
            ])
            for name, result in results:
                status[name] = result

        # Mark unconfigured integrations
        for name in ("github", "llm", "search", "instantly"):
            if name not in status:
                status[name] = "not_configured"

        return status

    def _generate_alerts(
        self,
        checks: list[AgentHealthCheck],
        budget: dict[str, Any],
        integrations: dict[str, str],
    ) -> list[str]:
        """Generate actionable alerts from health data."""
        alerts = []

        stale = [c for c in checks if c.status == "stale"]
        if stale:
            agents = ", ".join(c.agent for c in stale)
            alerts.append(f"STALE: {agents} have no recent output")

        # Any state that isn't healthy ("connected") or intentionally absent
        # ("not_configured") is a real alert: error_405, error_500,
        # unreachable: ConnectionError, etc.
        failed_integrations = [
            f"{k}={v}" for k, v in integrations.items()
            if v not in ("connected", "not_configured")
        ]
        if failed_integrations:
            alerts.append(
                f"INTEGRATION: {', '.join(failed_integrations)} unhealthy"
            )

        # Budget alerts
        total_tokens = budget.get("total_input_tokens", 0) + budget.get(
            "total_output_tokens", 0
        )
        if total_tokens > 500_000:
            alerts.append(f"BUDGET: High token usage ({total_tokens:,} total)")

        return alerts

    def _compute_health_score(
        self,
        checks: list[AgentHealthCheck],
        integrations: dict[str, str],
    ) -> int:
        """Compute overall system health score (0-100)."""
        score = 100

        # Deduct for stale/failed agents
        for c in checks:
            if c.status == "stale":
                score -= 5
            elif c.status == "failed":
                score -= 10
            elif c.status == "unknown":
                score -= 3

        # Deduct for integration issues. "not_configured" is intentional
        # (no key set), so it's a smaller deduction than an actual failure.
        for status in integrations.values():
            if status == "not_configured":
                score -= 2
            elif status != "connected":
                score -= 5

        return max(0, score)
