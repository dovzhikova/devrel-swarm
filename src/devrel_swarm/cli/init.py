"""`devrel init` command — bootstrap .devrel/ in cwd, then optionally chain
into the interactive onboarding wizard (auth → doctor → voice edit → first draft).

Chain behavior:
- Default (interactive): scaffold + run the chain
- `--non-interactive`: scaffold only, never prompt (CI shape, unchanged)
- `--skip-chain`: scaffold only, even in interactive mode
- `--skip-draft`: run the chain through doctor + voice edit but stop before
  the LLM call (no spend, no network)
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import typer
from dotenv import set_key
from rich.console import Console

from devrel_swarm.project.init import InitOptions, init_project
from devrel_swarm.project.paths import ProjectPaths

console = Console()


def init_command(
    name: str = typer.Option(
        ...,
        "--name",
        prompt="Project name (e.g., 'openclaw')",
        help="The product this devrel-swarm instance covers.",
    ),
    url: str = typer.Option(
        "",
        "--url",
        prompt="Project URL (or empty)",
        help="Public homepage URL for the product. Optional.",
    ),
    github_repo: str = typer.Option(
        "",
        "--github-repo",
        prompt="GitHub repo as 'owner/name' (or empty)",
        help="Optional. Used by Sage for issue triage.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be created without writing anything.",
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Skip prompts. Requires --name (others default to empty/null). Implies --skip-chain.",
    ),
    skip_chain: bool = typer.Option(
        False,
        "--skip-chain",
        help="Scaffold only; do not chain into the auth/doctor/draft wizard.",
    ),
    skip_draft: bool = typer.Option(
        False,
        "--skip-draft",
        help="Run the chain through health check + voice edit, but stop before the first LLM call.",
    ),
) -> None:
    """Bootstrap a `.devrel/` scaffold in the current directory and onboard you
    through the first run.

    The chain after scaffold:
      1. Configure an LLM key (Anthropic or OpenRouter)
      2. Run a health check
      3. Optionally edit voice.md to capture your tone
      4. Generate your first content draft

    Use --skip-chain to keep the old scaffold-only behavior. CI scripts that
    pass --non-interactive get scaffold-only automatically.
    """
    if non_interactive and not name:
        console.print("[red]--non-interactive requires --name.[/red]")
        raise typer.Exit(code=2)

    opts = InitOptions(
        name=name,
        url=url,
        github_repo=github_repo or None,
        dry_run=dry_run,
    )
    result = init_project(Path.cwd(), opts)

    if result.dry_run:
        console.print("[yellow]Dry run — nothing written.[/yellow]")
        for entry in result.would_create:
            console.print(f"  + {entry}")
        return

    for entry in result.created:
        console.print(f"  [green]+[/green] {entry}")
    for entry in result.skipped:
        console.print(f"  [dim]= {entry} (existed; preserved)[/dim]")
    console.print()
    console.print(f"[bold green]✓[/bold green] Scaffolded .devrel/ for [cyan]{name}[/cyan].")

    if non_interactive or skip_chain:
        # Scaffold-only path. Print the manual next-steps so users who skipped
        # the chain still know what to do.
        console.print()
        console.print("Next steps (run interactively for the guided wizard):")
        console.print(
            "  1. [cyan]devrel auth[/cyan]    configure your LLM API key (Anthropic or OpenRouter)"
        )
        console.print("  2. [cyan]devrel doctor[/cyan]  verify everything is wired up")
        console.print('  3. [cyan]devrel content draft "..."[/cyan]  ship your first draft')
        console.print(
            "[dim]Tip: OpenRouter offers free monthly credits and supports per-agent "
            "model routing. Sign up at https://openrouter.ai/.[/dim]"
        )
        return

    paths = ProjectPaths.from_root(Path.cwd())
    _run_onboarding_chain(paths, skip_draft=skip_draft)


def _run_onboarding_chain(paths: ProjectPaths, *, skip_draft: bool) -> None:
    """Walk the user from a fresh .devrel/ to a first content draft.

    Each step is independently skippable. A 'no' on any step prints the manual
    command the user can run later.
    """
    console.print()
    console.print("[bold]Let's get you to your first draft.[/bold]")
    console.print("[dim]Estimated 3-5 minutes. Skip any step with 'n'.[/dim]")

    if not _step_auth(paths):
        return
    if not _step_doctor(paths):
        return
    _step_edit_voice(paths)
    if not skip_draft:
        _step_first_draft(paths)
    else:
        console.print()
        console.print(
            '[dim]Skipped first draft (--skip-draft). Run `devrel content draft "..."` '
            "when ready.[/dim]"
        )


def _step_auth(paths: ProjectPaths) -> bool:
    """Step 2: configure an LLM key, or detect an existing one. Returns False
    if the user opts out and the chain should stop."""
    from devrel_swarm.cli.auth import (
        KEY_VAR,
        PROVIDER_ANTHROPIC,
        _ensure_env_file,
        _existing_key,
        _resolve_key,
        _resolve_provider,
        _validate,
    )

    console.print()
    console.print("[bold]Step 1 of 4: LLM provider[/bold]")

    # Detect any pre-existing key (from a prior init or manually-edited .env)
    # and short-circuit auth if found, so re-running init doesn't ask again.
    for existing_var in KEY_VAR.values():
        if _existing_key(paths.env_file, existing_var):
            console.print(
                f"[green]✓[/green] {existing_var} already configured in "
                f".devrel/.env. Use [cyan]devrel auth --rotate[/cyan] to replace it."
            )
            return True

    do_auth = typer.confirm("Configure your LLM key now?", default=True)
    if not do_auth:
        console.print(
            "[dim]Skipping. Run [cyan]devrel auth[/cyan] later to configure the key.[/dim]"
        )
        return False

    chosen = _resolve_provider(None, non_interactive=False)
    var = KEY_VAR[chosen]
    new_key = _resolve_key(
        chosen,
        arg=None,
        non_interactive=False,
        rotating=False,
        existing="",
    )
    if not new_key:
        console.print("[red]Empty key; skipping.[/red]")
        return False

    console.print(f"Validating {var} against {chosen}...")
    ok, err = asyncio.run(_validate(chosen, new_key))
    if not ok:
        console.print(f"[red]Validation failed:[/red] {err}")
        retry = typer.confirm("Save the key anyway (skip validation)?", default=False)
        if not retry:
            console.print("[dim]Skipping. Fix the key and run [cyan]devrel auth[/cyan].[/dim]")
            return False
    else:
        console.print("[green]✓[/green] key validated")

    _ensure_env_file(paths.env_file)
    set_key(str(paths.env_file), var, new_key, quote_mode="never")
    try:
        paths.env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    # Surface the key in the current process env so the draft step in this
    # same run can use it without restarting the shell.
    os.environ[var] = new_key

    masked = new_key[:4] + "..." + new_key[-4:] if len(new_key) > 8 else "***"
    console.print(f"[green]✓[/green] saved {var}={masked} to .devrel/.env (mode 0600)")
    if chosen == PROVIDER_ANTHROPIC:
        console.print(
            "[dim]Tip: switch providers with `devrel auth --provider openrouter` "
            "(free credits at https://openrouter.ai/).[/dim]"
        )
    return True


def _step_doctor(paths: ProjectPaths) -> bool:
    """Step 3: run health checks inline. Returns False if user aborts on
    failures."""
    from devrel_swarm.cli.doctor import _emit_pretty, _overall, _run_checks

    console.print()
    console.print("[bold]Step 2 of 4: Health check[/bold]")
    results = _run_checks(paths)
    overall = _overall(results)
    _emit_pretty(results, overall)

    if overall == "fail":
        console.print()
        proceed = typer.confirm(
            "Some checks failed. Continue with voice edit + first draft anyway?",
            default=False,
        )
        if not proceed:
            console.print(
                "[dim]Stopping. Fix the failing checks and re-run [cyan]devrel doctor[/cyan].[/dim]"
            )
            return False
    return True


def _step_edit_voice(paths: ProjectPaths) -> None:
    """Step 4: open voice.md in $EDITOR. Optional but pushed by default
    because un-edited voice.md produces generic output."""
    console.print()
    console.print("[bold]Step 3 of 4: Make it sound like you[/bold]")
    console.print(
        "Drop 3-5 short sample passages from your best published content into "
        "[cyan]voice.md[/cyan]. The persona pass + Sentinel use them to detect drift."
    )
    do_edit = typer.confirm(
        f"Open .devrel/voice.md in {os.environ.get('EDITOR', 'vi')} now?",
        default=True,
    )
    if not do_edit:
        console.print(
            "[dim]Skipping. Edit [cyan].devrel/voice.md[/cyan] later when you're ready. "
            "Same goes for style.md and slop-blocklist.md.[/dim]"
        )
        return

    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(paths.voice_file)], check=False)
    except FileNotFoundError:
        console.print(
            f"[yellow]Editor [/yellow][cyan]{editor}[/cyan][yellow] not found. "
            f"Edit .devrel/voice.md manually later.[/yellow]"
        )
        return
    console.print(
        "[green]✓[/green] voice.md edited. Repeat for [cyan]style.md[/cyan] and "
        "[cyan]slop-blocklist.md[/cyan] when you have time."
    )


def _step_first_draft(paths: ProjectPaths) -> None:
    """Step 5: generate a real content draft via Kai. Costs an API call."""
    console.print()
    console.print("[bold]Step 4 of 4: First content draft[/bold]")
    console.print(
        "[dim]This calls your LLM provider once (~30s, a few cents). Skip with 'n' "
        "to finish onboarding without an API call.[/dim]"
    )
    do_draft = typer.confirm("Generate your first content draft now?", default=True)
    if not do_draft:
        console.print(
            '[dim]Skipping. Run [cyan]devrel content draft "..."[/cyan] when ready.[/dim]'
        )
        _print_done_summary(paths)
        return

    topic = typer.prompt("Topic or prompt").strip()
    if not topic:
        console.print("[yellow]Empty topic; skipping draft.[/yellow]")
        _print_done_summary(paths)
        return

    content_type = (
        typer.prompt(
            "Content type (tutorial / blog_post / landing_page / cold_email / battle_card)",
            default="tutorial",
        ).strip()
        or "tutorial"
    )

    # Build Kai inline using the same wiring as `devrel content draft`. We
    # call into the existing draft_command via Typer's invocation mechanism
    # would print twice; easier to import the helpers and replay the body.
    from devrel_swarm.cli.content import _build_kai, _build_llm_client, _slug, _write_outputs

    # _build_llm_client now resolves the LLM key from .devrel/.env (Anthropic
    # OR OpenRouter) and raises typer.Exit(1) with a helpful message if neither
    # is configured. Letting Exit propagate exits the wizard cleanly with the
    # missing-key help; the user can then run `devrel auth` and re-run.
    client = _build_llm_client(paths)
    kai = _build_kai(paths, client)

    console.print(f"[dim]Generating draft on '{topic[:60]}'...[/dim]")

    async def _do() -> None:
        result = await kai.execute(task=topic, content_type=content_type)
        status = result.get("status")
        body = result.get("content") or ""
        if status != "generated" or not body:
            console.print(f"[red]Kai did not produce content (status={status}).[/red]")
            for gap in result.get("evidence_gaps", []):
                console.print(f"  - {gap}")
            if result.get("error"):
                console.print(f"  error: {result['error']}")
            return

        trace = {
            "agent": "kai",
            "task": result.get("task"),
            "content_type": content_type,
            "grounding_sources": result.get("grounding_sources", []),
            "pain_points_addressed": result.get("pain_points_addressed", []),
            "real_issues_referenced": result.get("real_issues_referenced", []),
            "revision": result.get("revision", {}),
            "code_validation": result.get("code_validation", {}),
        }
        body_path, trace_path = _write_outputs(paths, _slug(topic), body, trace)
        console.print(f"[green]✓[/green] Wrote {body_path.name} ({len(body)} chars)")
        console.print(f"[green]✓[/green] Wrote {trace_path.name}")

        sources = result.get("grounding_sources") or []
        if not sources:
            console.print(
                "[yellow]⚠[/yellow] No KB sources matched the prompt; output may be "
                "ungrounded. Run [cyan]devrel kb add <url>[/cyan] to populate the KB."
            )

    asyncio.run(_do())
    _print_done_summary(paths)


def _print_done_summary(paths: ProjectPaths) -> None:
    """Closing message regardless of which step the chain ended at."""
    console.print()
    console.print("[bold green]✓ Onboarding complete.[/bold green]")
    console.print()
    console.print("Where to go next:")
    console.print("  • Read your draft:  [cyan]ls .devrel/deliverables/[/cyan]")
    console.print("  • Populate the KB:  [cyan]devrel kb add https://yourdocs.com[/cyan]")
    console.print(
        "  • Tune editorial:   edit [cyan].devrel/style.md[/cyan] + "
        "[cyan].devrel/slop-blocklist.md[/cyan]"
    )
    console.print("  • Full weekly run:  [cyan]devrel run[/cyan]")
    console.print()
    console.print("[dim]Stuck? See docs/troubleshooting.md.[/dim]")
