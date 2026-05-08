"""`devrel doctor` — health checks for the current project."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass

import typer
from rich.console import Console

from devrel_swarm.project.config import ConfigError, ProjectConfig
from devrel_swarm.project.paths import (
    ProjectNotFoundError,
    ProjectPaths,
    find_devrel_root,
)
from devrel_swarm.project.state import SCHEMA_VERSION, get_schema_version

console = Console()

# LLM key requirement is one-of: Anthropic direct OR OpenRouter (multi-provider).
LLM_KEY_OPTIONS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
OPTIONAL_ENV = (
    "GITHUB_TOKEN",
    "FIRECRAWL_API_KEY",
    "BRAVE_API_KEY",
    "INSTANTLY_API_KEY",
    "APOLLO_API_KEY",
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)


@dataclass
class CheckResult:
    name: str
    status: str  # 'pass' | 'warn' | 'fail'
    detail: str = ""


def _run_checks(paths: ProjectPaths) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Python version.
    py = sys.version_info
    py_str = f"{py.major}.{py.minor}.{py.micro}"
    if (py.major, py.minor) >= (3, 12):
        results.append(CheckResult("python_version", "pass", py_str))
    else:
        results.append(CheckResult("python_version", "fail", f"{py_str} (requires >=3.12)"))

    # Required files.
    for label, fp in [
        ("config.toml", paths.config_file),
        ("voice.md", paths.voice_file),
        ("style.md", paths.style_file),
        ("slop-blocklist.md", paths.slop_file),
    ]:
        if fp.is_file():
            results.append(CheckResult(label, "pass"))
        else:
            results.append(CheckResult(label, "fail", f"missing at {fp}"))

    # Config parses.
    if paths.config_file.is_file():
        try:
            cfg = ProjectConfig.load(paths.config_file)
            results.append(CheckResult("config_parses", "pass", f"project={cfg.project.name}"))
        except ConfigError as e:
            results.append(CheckResult("config_parses", "fail", str(e)))

    # State DB.
    sv = get_schema_version(paths.state_db)
    if sv is None:
        results.append(CheckResult("state_db", "fail", "missing or unreadable; run `devrel init`"))
    elif sv == SCHEMA_VERSION:
        results.append(CheckResult("state_db", "pass", f"schema v{sv}"))
    else:
        results.append(
            CheckResult(
                "state_db",
                "warn",
                f"schema v{sv}, current is v{SCHEMA_VERSION}; run `devrel migrate`",
            )
        )

    # LLM key: at least one of Anthropic direct or OpenRouter must be set.
    # Pull keys out of .devrel/.env (or root .env) first so a user who set
    # them via `devrel auth` and hasn't restarted their shell still passes.
    from devrel_swarm.cli._common import _load_project_env

    _load_project_env(paths)
    set_keys = [n for n in LLM_KEY_OPTIONS if os.environ.get(n)]
    if set_keys:
        results.append(CheckResult("llm_api_key", "pass", f"set: {', '.join(set_keys)}"))
    else:
        results.append(
            CheckResult(
                "llm_api_key",
                "fail",
                "no LLM key set; run `devrel auth` (Anthropic or OpenRouter)",
            )
        )

    # Optional env.
    for name in OPTIONAL_ENV:
        val = os.environ.get(name)
        results.append(
            CheckResult(name, "pass" if val else "warn", "set" if val else "not set (optional)")
        )

    # KB freshness.
    if paths.kb_dir.is_dir():
        n = sum(1 for _ in paths.kb_dir.rglob("*.md"))
        results.append(CheckResult("kb_files", "pass" if n > 0 else "warn", f"{n} markdown files"))
    else:
        results.append(CheckResult("kb_files", "warn", "kb/ missing"))

    return results


def _overall(results: list[CheckResult]) -> str:
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "ok"


def _emit_pretty(results: list[CheckResult], overall: str) -> None:
    icons = {"pass": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✗[/red]"}
    for r in results:
        console.print(f"  {icons[r.status]} {r.name:<24} {r.detail}")
    console.print()
    label = {
        "ok": "[bold green]All checks passed.[/bold green]",
        "warn": "[bold yellow]Some warnings; nothing blocking.[/bold yellow]",
        "fail": "[bold red]One or more checks failed.[/bold red]",
    }[overall]
    console.print(label)


def _emit_json(results: list[CheckResult], overall: str) -> None:
    typer.echo(
        json.dumps(
            {"status": overall, "checks": [asdict(r) for r in results]},
            indent=2,
        )
    )


def doctor_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of pretty output.",
    ),
) -> None:
    """Run health checks on the current project."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    paths = ProjectPaths.from_root(root)
    results = _run_checks(paths)
    overall = _overall(results)

    if json_output:
        _emit_json(results, overall)
    else:
        _emit_pretty(results, overall)

    if overall == "fail":
        raise typer.Exit(code=1)
