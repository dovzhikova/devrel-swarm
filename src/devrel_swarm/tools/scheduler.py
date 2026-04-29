"""
Scheduler — Cron-based agent pipeline scheduling.

Manages the weekly agent cascade schedule. Can install/remove system
crontab entries or run as a standalone loop.
"""

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScheduleEntry:
    """A single scheduled agent run."""

    name: str
    cron: str  # Cron expression (e.g., "0 9 * * 1")
    command: str  # Full command to execute
    description: str = ""
    enabled: bool = True


# Default weekly schedule matching the cascade system
DEFAULT_SCHEDULE: list[dict[str, str]] = [
    {
        "name": "weekly_cycle",
        "cron": "0 9 * * 1",  # Monday 9am
        "command": "python -m devrel_swarm.core.atlas --weekly-cycle",
        "description": "Full weekly pipeline",
    },
    {
        "name": "daily_digest",
        "cron": "0 13 * * 1-5",  # Mon-Fri 1pm
        "command": "python -m devrel_swarm.tools.scheduler --action digest --mode daily",
        "description": "Daily content digest email + telegram",
    },
    {
        "name": "weekly_report",
        "cron": "0 17 * * 5",  # Friday 5pm
        "command": "python -m devrel_swarm.tools.scheduler --action digest --mode weekly",
        "description": "Weekly report email + telegram",
    },
]


class Scheduler:
    """Manages cron-based agent scheduling.

    Can install entries into the system crontab or run as a standalone
    asyncio loop for environments without cron access.

    Usage::

        sched = Scheduler(project_dir="/path/to/devrel-swarm")
        sched.install_cron()  # Write to system crontab
        sched.list_entries()  # Show current schedule
        sched.remove_cron()   # Clean up
    """

    CRON_TAG = "# devrel-swarm-agent"

    def __init__(
        self,
        project_dir: str = ".",
        schedule: list[dict[str, str]] | None = None,
        python_path: str = "",
    ):
        self.project_dir = Path(project_dir).resolve()
        self.python_path = python_path or sys.executable
        self.entries = [
            ScheduleEntry(**entry)
            for entry in (schedule or DEFAULT_SCHEDULE)
        ]

    def _build_cron_line(self, entry: ScheduleEntry) -> str:
        """Build a crontab line from a schedule entry."""
        cmd = entry.command
        if not cmd.startswith("/"):
            cmd = f"cd {self.project_dir} && {self.python_path} -m {cmd.split('python -m ')[-1]}"
        return f"{entry.cron} {cmd} {self.CRON_TAG} {entry.name}"

    def get_current_crontab(self) -> str:
        """Read the current user crontab."""
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, check=False,
            )
            return result.stdout if result.returncode == 0 else ""
        except FileNotFoundError:
            logger.warning("crontab not available on this system")
            return ""

    def install_cron(self) -> list[str]:
        """Install schedule entries into the system crontab.

        Removes existing devrel-swarm entries first to prevent duplicates.
        Returns the list of installed cron lines.
        """
        current = self.get_current_crontab()

        # Remove existing devrel-swarm entries
        cleaned_lines = [
            line for line in current.splitlines()
            if self.CRON_TAG not in line
        ]

        # Add new entries
        new_lines = []
        for entry in self.entries:
            if entry.enabled:
                line = self._build_cron_line(entry)
                new_lines.append(line)

        all_lines = cleaned_lines + new_lines
        new_crontab = "\n".join(all_lines) + "\n"

        try:
            subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True, check=True,
            )
            logger.info(f"Installed {len(new_lines)} cron entries")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning(f"Failed to install crontab: {exc}")

        return new_lines

    def remove_cron(self) -> None:
        """Remove all devrel-swarm entries from the crontab."""
        current = self.get_current_crontab()
        cleaned = [
            line for line in current.splitlines()
            if self.CRON_TAG not in line
        ]
        new_crontab = "\n".join(cleaned) + "\n"

        try:
            subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True, check=True,
            )
            logger.info("Removed all devrel-swarm cron entries")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning(f"Failed to update crontab: {exc}")

    def list_entries(self) -> list[dict[str, Any]]:
        """List all configured schedule entries."""
        return [
            {
                "name": e.name,
                "cron": e.cron,
                "description": e.description,
                "enabled": e.enabled,
                "command": e.command,
            }
            for e in self.entries
        ]


async def run_digest(mode: str = "daily") -> None:
    """CLI entry point for sending content digests."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    from devrel_swarm.tools.notifications import NotificationConfig, NotificationService

    config = NotificationConfig(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        email_sender=os.environ.get("EMAIL_SENDER", ""),
        email_password=os.environ.get("EMAIL_PASSWORD", ""),
        email_recipients=(
            os.environ.get("EMAIL_RECIPIENTS", "").split(",")
            if os.environ.get("EMAIL_RECIPIENTS") else None
        ),
    )

    from devrel_swarm.core.atlas import SharedContext

    archive_dir = Path(os.environ.get("CONTEXT_ARCHIVE", "context_archive"))
    ctx = SharedContext.load(archive_dir)

    svc = NotificationService(config)
    try:
        result = await svc.send_digest(ctx.to_dict(), mode=mode)
        logger.info(f"Digest sent: {result}")
    finally:
        await svc.close()


def main() -> None:
    """CLI entry point for scheduler operations."""
    import argparse

    parser = argparse.ArgumentParser(description="devrel-swarm scheduler")
    parser.add_argument(
        "--action",
        choices=["install", "remove", "list", "digest"],
        required=True,
    )
    parser.add_argument("--mode", default="daily", choices=["daily", "weekly"])
    args = parser.parse_args()

    if args.action == "digest":
        asyncio.run(run_digest(args.mode))
    elif args.action == "install":
        sched = Scheduler()
        lines = sched.install_cron()
        for line in lines:
            print(f"  {line}")
    elif args.action == "remove":
        sched = Scheduler()
        sched.remove_cron()
    elif args.action == "list":
        sched = Scheduler()
        for entry in sched.list_entries():
            status = "✓" if entry["enabled"] else "✗"
            print(f"  [{status}] {entry['name']}: {entry['cron']} — {entry['description']}")


if __name__ == "__main__":
    main()
