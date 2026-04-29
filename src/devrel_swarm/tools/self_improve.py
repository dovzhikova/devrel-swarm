"""
Self-Improvement — Extract recurring quality issues and feed back into agent prompts.

Analyzes Sentinel audit reports across recent weeks to find patterns.
Generates per-agent "known issues" addenda that are automatically appended
to agent system prompts via the optimize/ directory.
"""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_recent_audits(
    archive_dir: Path, weeks: int = 4,
) -> list[dict[str, Any]]:
    """Load Sentinel audit results from recent context archives."""
    audits = []
    files = sorted(archive_dir.glob("context_*.json"), reverse=True)

    for f in files[:weeks]:
        if "_stage" in f.name:
            continue
        try:
            data = json.loads(f.read_text())
            okr = data.get("okr_progress", {})
            audit = okr.get("brand_audit", {})
            if audit and "items" in audit:
                audit["_week"] = data.get("week_of", f.stem)
                audits.append(audit)
        except (json.JSONDecodeError, OSError):
            continue

    return audits


def extract_recurring_issues(
    audits: list[dict[str, Any]], min_occurrences: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Find issues that recur across multiple weeks, grouped by agent.

    Returns a dict of agent_name → list of recurring issues with counts.
    """
    # Collect all issues per agent
    agent_issues: dict[str, list[str]] = {}
    for audit in audits:
        for item in audit.get("items", []):
            agent = item.get("agent", "unknown")
            for issue in item.get("issues", []):
                desc = issue.get("detail", issue.get("description", ""))
                if desc:
                    agent_issues.setdefault(agent, []).append(desc.lower().strip())

    # Count occurrences per agent
    recurring: dict[str, list[dict[str, Any]]] = {}
    for agent, issues in agent_issues.items():
        counts = Counter(issues)
        frequent = [
            {"issue": issue, "occurrences": count}
            for issue, count in counts.most_common()
            if count >= min_occurrences
        ]
        if frequent:
            recurring[agent] = frequent[:5]

    return recurring


def generate_prompt_addenda(
    recurring: dict[str, list[dict[str, Any]]],
    optimize_dir: Path,
) -> dict[str, Path]:
    """Write per-agent known-issues files to the optimize directory.

    These are picked up by load_agent_prompt() as supplementary context.
    Existing addenda are overwritten each cycle.

    Returns a dict of agent_name → file path written.
    """
    written: dict[str, Path] = {}

    for agent, issues in recurring.items():
        agent_dir = optimize_dir / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        filepath = agent_dir / "known_issues.txt"
        lines = [
            "## Known Quality Issues (auto-generated from Sentinel audits)\n",
            "Avoid these recurring problems in your output:\n",
        ]
        for item in issues:
            lines.append(
                f"- {item['issue']} (flagged {item['occurrences']} times)\n"
            )
        lines.append(
            "\nThese issues have been identified across multiple weekly cycles. "
            "Actively work to avoid them.\n"
        )

        filepath.write_text("".join(lines))
        written[agent] = filepath
        logger.info(f"Wrote known_issues addendum for {agent}: {filepath}")

    return written


def run_self_improvement(
    archive_dir: Path,
    optimize_dir: Path,
    weeks: int = 4,
    min_occurrences: int = 2,
) -> dict[str, Any]:
    """Full self-improvement cycle.

    1. Load recent Sentinel audits
    2. Extract recurring issues per agent
    3. Write prompt addenda to optimize/

    Returns a report of what was found and written.
    """
    audits = load_recent_audits(archive_dir, weeks)
    if not audits:
        logger.info("No recent audits found for self-improvement")
        return {"audits_analyzed": 0, "recurring_issues": {}, "files_written": {}}

    recurring = extract_recurring_issues(audits, min_occurrences)
    written = generate_prompt_addenda(recurring, optimize_dir)

    report = {
        "audits_analyzed": len(audits),
        "recurring_issues": {
            agent: [i["issue"] for i in issues]
            for agent, issues in recurring.items()
        },
        "files_written": {agent: str(path) for agent, path in written.items()},
    }

    logger.info(
        f"Self-improvement: analyzed {len(audits)} audits, "
        f"found {sum(len(v) for v in recurring.values())} recurring issues "
        f"across {len(recurring)} agents"
    )
    return report


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Agent self-improvement from Sentinel audits")
    parser.add_argument("--archive", default="context_archive", help="Archive directory")
    parser.add_argument("--optimize", default="optimize", help="Optimize directory")
    parser.add_argument("--weeks", type=int, default=4, help="Weeks to analyze")
    args = parser.parse_args()

    report = run_self_improvement(
        Path(args.archive), Path(args.optimize), args.weeks,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
