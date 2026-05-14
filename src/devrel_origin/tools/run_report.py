"""
Run Report — Structured observability for weekly pipeline cycles.

Generates a JSON report after each cycle with timing, cost, quality,
and error data. Stored alongside context archives for post-hoc analysis.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentTiming:
    """Timing data for a single agent delegation."""

    agent: str
    stage: int
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    success: bool = True
    error: str = ""


@dataclass
class RunReport:
    """Complete run report for a weekly cycle."""

    week_of: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    resumed_from_stage: int = 0
    stages_completed: int = 0
    agent_timings: list[AgentTiming] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_of": self.week_of,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 1),
            "resumed_from_stage": self.resumed_from_stage,
            "stages_completed": self.stages_completed,
            "agent_timings": [
                {
                    "agent": t.agent,
                    "stage": t.stage,
                    "started_at": t.started_at,
                    "completed_at": t.completed_at,
                    "duration_seconds": round(t.duration_seconds, 1),
                    "success": t.success,
                    "error": t.error,
                }
                for t in self.agent_timings
            ],
            "cost": self.cost,
            "quality": self.quality,
            "health": self.health,
            "errors": self.errors,
        }

    def save(self, archive_dir: Path) -> Path:
        """Save report alongside context archive."""
        archive_dir.mkdir(parents=True, exist_ok=True)
        filepath = archive_dir / f"run_report_{self.week_of}.json"
        filepath.write_text(json.dumps(self.to_dict(), indent=2))
        logger.info(f"Run report saved: {filepath}")
        return filepath

    @classmethod
    def load(cls, archive_dir: Path, week_of: str = "") -> "RunReport | None":
        """Load a run report by week, or the most recent one."""
        if week_of:
            filepath = archive_dir / f"run_report_{week_of}.json"
            if filepath.exists():
                data = json.loads(filepath.read_text())
                return cls._from_dict(data)
            return None

        # Find most recent
        files = sorted(archive_dir.glob("run_report_*.json"), reverse=True)
        if not files:
            return None
        data = json.loads(files[0].read_text())
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "RunReport":
        report = cls(
            week_of=data.get("week_of", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            duration_seconds=data.get("duration_seconds", 0),
            resumed_from_stage=data.get("resumed_from_stage", 0),
            stages_completed=data.get("stages_completed", 0),
            cost=data.get("cost", {}),
            quality=data.get("quality", {}),
            health=data.get("health", {}),
            errors=data.get("errors", []),
        )
        for t in data.get("agent_timings", []):
            report.agent_timings.append(
                AgentTiming(
                    agent=t["agent"],
                    stage=t.get("stage", 0),
                    started_at=t.get("started_at", ""),
                    completed_at=t.get("completed_at", ""),
                    duration_seconds=t.get("duration_seconds", 0),
                    success=t.get("success", True),
                    error=t.get("error", ""),
                )
            )
        return report

    def summary(self) -> str:
        """Human-readable summary for CLI output."""
        lines = [
            f"Run Report — {self.week_of}",
            f"Duration: {self.duration_seconds:.0f}s"
            f"  |  Stages: {self.stages_completed}"
            f"  |  Resume: {self.resumed_from_stage}",
            "",
        ]

        # Cost
        cost = self.cost
        if cost:
            lines.append(
                f"Cost: ${cost.get('total_cost_usd', 0):.4f} "
                f"/ ${cost.get('budget_limit_usd', 0):.2f} budget"
            )
            per_agent = cost.get("per_agent", {})
            if per_agent:
                sorted_agents = sorted(
                    per_agent.items(),
                    key=lambda x: x[1].get("cost_usd", 0),
                    reverse=True,
                )
                for name, data in sorted_agents[:5]:
                    lines.append(
                        f"  {name}: ${data.get('cost_usd', 0):.4f} ({data.get('calls', 0)} calls)"
                    )

        # Quality
        quality = self.quality
        if quality:
            lines.append("")
            lines.append(f"Quality: Sentinel score {quality.get('sentinel_score', 'N/A')}/100")
            for agent, rev in quality.get("revision_traces", {}).items():
                lines.append(
                    f"  {agent}: score {rev.get('final_score', '?')}/10 "
                    f"({rev.get('rounds', 0)} revisions)"
                )

        # Errors
        if self.errors:
            lines.append("")
            lines.append(f"Errors ({len(self.errors)}):")
            for err in self.errors[:5]:
                lines.append(f"  - {err}")

        return "\n".join(lines)


def main() -> None:
    """CLI: view run reports."""
    import argparse

    parser = argparse.ArgumentParser(description="View pipeline run reports")
    parser.add_argument("--week", default="", help="Week to view (e.g., 2026-W14)")
    parser.add_argument("--archive", default="context_archive", help="Archive directory")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    report = RunReport.load(Path(args.archive), args.week)
    if not report:
        print("No run report found.")
        return

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())


if __name__ == "__main__":
    main()
