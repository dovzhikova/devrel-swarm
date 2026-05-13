"""`devrel docs build` - AST-based docs via Dex."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from devrel_origin.cli._common import build_atlas_or_exit, find_paths_or_exit, render_result

console = Console()

docs_app = typer.Typer(
    name="docs",
    help="Documentation generation.",
    no_args_is_help=True,
    add_completion=False,
)


def _persist_dex_output(output: dict[str, Any], deliverables_dir: Path) -> list[Path]:
    """Write Dex's architecture / API / summary / modules outputs to disk.

    Returns the list of files actually written (skips empty values). Used by
    `devrel docs build` so users don't have to invoke with --json and split
    the JSON blob themselves; Dex's architecture_doc is ~36KB and api_reference
    is ~270KB on a real codebase, both unreadable through the truncated render.
    """
    deliverables_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def _write_text(name: str, value: Any) -> None:
        if not value or not isinstance(value, str):
            return
        path = deliverables_dir / name
        path.write_text(value)
        written.append(path)

    _write_text("dex-architecture.md", output.get("architecture_doc"))
    _write_text("dex-api-reference.md", output.get("api_reference"))
    _write_text("dex-summary.md", output.get("llm_summary"))

    # Modules + languages travel as structured data, so persist as JSON for
    # downstream tooling. Skip if absent or empty.
    modules = output.get("modules")
    if modules:
        manifest = {
            "languages": output.get("languages", {}),
            "modules": modules,
            "status": output.get("status", "generated"),
        }
        path = deliverables_dir / "dex-modules.json"
        path.write_text(json.dumps(manifest, indent=2, default=str))
        written.append(path)

    return written


@docs_app.command("build")
def build(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build architecture docs + API reference from source via Dex.

    Successful runs persist architecture_doc / api_reference / llm_summary
    to .devrel/deliverables/dex-*.md so users don't have to pipe --json and
    split the blob manually.
    """
    paths = find_paths_or_exit(console)
    atlas = build_atlas_or_exit(paths, console)

    async def _do() -> None:
        result = await atlas.run_single_task("dex", "Build architecture docs and API reference")
        if result.success and isinstance(result.output, dict):
            written = _persist_dex_output(result.output, paths.deliverables_dir)
            if written and not json_output:
                console.print(f"[green]✓[/green] dex completed; wrote {len(written)} file(s):")
                for p in written:
                    try:
                        rel = p.relative_to(paths.root)
                        console.print(f"  [dim]-[/dim] {rel}")
                    except ValueError:
                        console.print(f"  [dim]-[/dim] {p}")
                return
        render_result(result, console, json_output=json_output)

    asyncio.run(_do())
