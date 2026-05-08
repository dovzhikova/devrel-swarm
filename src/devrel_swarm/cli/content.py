"""`devrel content draft|audit` — primary entry points to the editorial pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console

from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectNotFoundError, ProjectPaths, find_devrel_root
from devrel_swarm.quality.editorial import AbortLoud, run_pipeline
from devrel_swarm.quality.slop import find_slop, parse_blocklist

console = Console()

content_app = typer.Typer(
    name="content",
    help="Generate and audit content through the editorial quality pipeline.",
    no_args_is_help=True,
    add_completion=False,
)


def _build_llm_client() -> LLMClient:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise typer.BadParameter("ANTHROPIC_API_KEY is required.")
    return LLMClient(api_key=api_key)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "draft"


def _write_outputs(paths: ProjectPaths, slug: str, body: str, trace: dict) -> tuple[Path, Path]:
    paths.deliverables_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    body_path = paths.deliverables_dir / f"{ts}-{slug}.md"
    trace_path = paths.deliverables_dir / f"{ts}-{slug}-trace.json"
    body_path.write_text(body)
    trace_path.write_text(json.dumps(trace, indent=2))
    return body_path, trace_path


@content_app.command("draft")
def draft_command(
    prompt: str = typer.Argument(..., help="Topic or instruction for the new content."),
    content_type: str = typer.Option(
        "tutorial",
        "--type",
        help="Content type for targeting (tutorial, blog_post, landing_page, cold_email, battle_card).",
    ),
) -> None:
    """Generate new content via the 8-stage editorial pipeline."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    paths = ProjectPaths.from_root(root)
    client = _build_llm_client()

    async def _do() -> None:
        draft = await client.generate(
            system_prompt=(
                "You are a writer producing a first draft. Stay specific and concrete. "
                "Avoid marketing fluff."
            ),
            user_prompt=prompt,
        )
        try:
            result = await run_pipeline(
                initial_draft=draft,
                content_type=content_type,
                project_paths=paths,
                llm_client=client,
            )
        except AbortLoud as e:
            console.print(f"[red]Pipeline aborted: {e}[/red]")
            raise typer.Exit(code=1) from None
        body_path, trace_path = _write_outputs(
            paths, _slug(prompt), result.final_text, result.revision_trace
        )
        console.print(f"[green]✓[/green] Wrote {body_path.name} ({len(result.final_text)} chars)")
        console.print(f"[green]✓[/green] Wrote {trace_path.name}")
        if result.flagged:
            console.print(
                "[yellow]⚠[/yellow] Flagged: persona or readability gates failed twice; output shipped anyway."
            )

    asyncio.run(_do())


@content_app.command("slop")
def slop_command(
    file: Path = typer.Argument(..., exists=True, readable=True, help="File to lint for slop."),
) -> None:
    """Run the deterministic regex slop blocklist against a file. Exits
    nonzero if any blocklisted phrase is hit. No LLM calls."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    paths = ProjectPaths.from_root(root)
    if not paths.slop_file.is_file():
        console.print(f"[red]No slop blocklist at {paths.slop_file}[/red]")
        raise typer.Exit(code=1)
    blocklist = parse_blocklist(paths.slop_file.read_text())
    text = file.read_text()
    hits = find_slop(text, blocklist)
    if not hits:
        console.print(
            f"[green]✓[/green] {file.name}: no slop hits ({len(blocklist)} phrases checked)"
        )
        return
    console.print(f"[red]✗[/red] {file.name}: {len(hits)} slop hit(s)")
    for h in hits[:50]:
        console.print(f"  [yellow]{h.phrase!r}[/yellow] at offset {h.start}")
    raise typer.Exit(code=1)


@content_app.command("audit")
def audit_command(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Existing draft to audit."),
    content_type: str = typer.Option("tutorial", "--type"),
) -> None:
    """Run the editorial pipeline against an existing draft file."""
    try:
        root = find_devrel_root()
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None
    paths = ProjectPaths.from_root(root)
    client = _build_llm_client()

    async def _do() -> None:
        body = file.read_text()
        try:
            result = await run_pipeline(
                initial_draft=body,
                content_type=content_type,
                project_paths=paths,
                llm_client=client,
            )
        except AbortLoud as e:
            console.print(f"[red]Pipeline aborted: {e}[/red]")
            raise typer.Exit(code=1) from None
        body_path, trace_path = _write_outputs(
            paths, _slug(file.stem), result.final_text, result.revision_trace
        )
        console.print(f"[green]✓[/green] Wrote {body_path.name}")
        console.print(f"[green]✓[/green] Wrote {trace_path.name}")
        if result.flagged:
            console.print("[yellow]⚠[/yellow] Flagged.")

    asyncio.run(_do())
