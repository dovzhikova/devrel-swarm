"""Shared CLI helpers."""

from __future__ import annotations

import json
import os

import typer
from rich.console import Console

from devrel_swarm.core.atlas import Atlas, DelegationResult
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectNotFoundError, ProjectPaths, find_devrel_root
from devrel_swarm.tools.api_client import PostHogClient


def find_paths_or_exit(console: Console) -> ProjectPaths:
    try:
        return ProjectPaths.from_root(find_devrel_root())
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


def build_atlas_or_exit(paths: ProjectPaths, console: Console) -> Atlas:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY is required.[/red]")
        raise typer.Exit(code=1)
    llm = LLMClient(api_key=api_key)
    posthog = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )
    return Atlas(
        api_client=posthog,
        knowledge_base_path=paths.kb_dir,
        llm_client=llm,
        project_paths=paths,
    )


def render_result(result: DelegationResult, console: Console, *, json_output: bool = False) -> None:
    if json_output:
        # DelegationResult is a dataclass; convert via dict()/asdict.
        from dataclasses import asdict

        try:
            payload = asdict(result)
        except TypeError:
            payload = {
                "agent": getattr(result, "agent", "?"),
                "task": getattr(result, "task", "?"),
                "success": getattr(result, "success", False),
                "output": getattr(result, "output", None),
                "error": getattr(result, "error", None),
            }
        typer.echo(json.dumps(payload, default=str, indent=2))
        return
    if not result.success:
        console.print(f"[red]✗[/red] {result.agent} failed: {result.error}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] {result.agent} completed")
    if isinstance(result.output, dict):
        for k, v in list(result.output.items())[:8]:
            console.print(f"  [dim]{k}:[/dim] {str(v)[:120]}")
    elif result.output:
        console.print(f"  {str(result.output)[:300]}")
