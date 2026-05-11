"""`devrel auth` - configure or rotate the LLM API key for this project.

Writes the chosen key to `.devrel/.env` (chmod 600) so subsequent CLI
commands pick it up via the auto-loader in `_common._load_project_env`.
Validates the key with a tiny ping call by default; pass `--no-validate`
to skip when offline or working with credit-metered keys.

Provider resolution:
- `--provider anthropic` or `--provider openrouter` is explicit
- Without `--provider`, prompts in interactive mode; defaults to anthropic
  in non-interactive mode (preserves prior CLI default)

Key handling:
- `--key VALUE` accepts the key on the command line (history risk; OK for CI)
- Without `--key`, prompts with hidden input
- `--rotate` lets the user replace an existing key for the same provider;
  without it, an existing key for the chosen provider blocks (use --rotate
  to overwrite or pick a different provider)
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import typer
from dotenv import dotenv_values, set_key
from rich.console import Console

from devrel_swarm.cli._common import find_paths_or_exit
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectPaths

console = Console()

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER)
KEY_VAR = {
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
    PROVIDER_OPENROUTER: "OPENROUTER_API_KEY",
}
SIGNUP_URL = {
    PROVIDER_ANTHROPIC: "https://console.anthropic.com/settings/keys",
    PROVIDER_OPENROUTER: "https://openrouter.ai/keys",
}


def _ensure_env_file(env_file: Path) -> None:
    """Touch .devrel/.env if it doesn't exist and lock it to 0600."""
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.is_file():
        env_file.touch()
    # chmod 600 unconditionally so the file is locked down even if it
    # pre-existed at a looser permission. POSIX-only; no-op on Windows
    # but the perm bits are advisory there anyway.
    try:
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _existing_key(env_file: Path, var: str) -> str:
    if not env_file.is_file():
        return ""
    return (dotenv_values(env_file).get(var) or "").strip()


async def _validate(provider: str, key: str) -> tuple[bool, str]:
    """One-token ping. Returns (ok, error_message)."""
    try:
        if provider == PROVIDER_ANTHROPIC:
            client = LLMClient(provider=PROVIDER_ANTHROPIC, api_key=key)
        else:
            client = LLMClient(provider=PROVIDER_OPENROUTER, openrouter_api_key=key)
        try:
            await client.generate(
                system_prompt="Reply with the single word: ok",
                user_prompt="ping",
                max_tokens=5,
                temperature=0.0,
            )
        finally:
            await client.aclose()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - surface any auth error to the user
        return False, str(exc)


def _resolve_provider(
    arg: str | None,
    *,
    non_interactive: bool,
) -> str:
    if arg:
        if arg not in PROVIDERS:
            console.print(
                f"[red]Unknown provider '{arg}'. Choose one of: {', '.join(PROVIDERS)}.[/red]"
            )
            raise typer.Exit(code=1)
        return arg
    if non_interactive:
        return PROVIDER_ANTHROPIC
    console.print("Pick an LLM provider:")
    console.print("  [bold]1[/bold]) anthropic   (https://console.anthropic.com/settings/keys)")
    console.print(
        "  [bold]2[/bold]) openrouter  (https://openrouter.ai/keys, free credits available)"
    )
    choice = typer.prompt("Provider [1/2]", default="1").strip()
    return PROVIDER_OPENROUTER if choice in ("2", "openrouter", "or") else PROVIDER_ANTHROPIC


def _resolve_key(
    provider: str,
    *,
    arg: str | None,
    non_interactive: bool,
    rotating: bool,
    existing: str,
) -> str:
    if arg:
        return arg.strip()
    if non_interactive:
        console.print(
            f"[red]--key is required in --non-interactive mode. "
            f"Pass --key <value> or set {KEY_VAR[provider]}.[/red]"
        )
        raise typer.Exit(code=1)
    if existing and not rotating:
        console.print(
            f"[yellow]A {KEY_VAR[provider]} is already set in .devrel/.env. "
            f"Pass --rotate to replace it, or pick a different provider.[/yellow]"
        )
        raise typer.Exit(code=1)
    label = "Paste new" if existing else "Paste"
    return typer.prompt(
        f"{label} {KEY_VAR[provider]} (input hidden)",
        hide_input=True,
    ).strip()


def auth_command(
    provider: str = typer.Option(
        "",
        "--provider",
        help="LLM provider: anthropic or openrouter. Prompts if omitted.",
    ),
    key: str = typer.Option(
        "",
        "--key",
        help="API key (skip the prompt; not recommended interactively).",
    ),
    rotate: bool = typer.Option(
        False, "--rotate", help="Replace an existing key for the same provider."
    ),
    no_validate: bool = typer.Option(
        False, "--no-validate", help="Skip the ping call that verifies the key."
    ),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Fail instead of prompting when --provider or --key is missing.",
    ),
) -> None:
    """Configure (or rotate) the LLM API key for this project."""
    paths: ProjectPaths = find_paths_or_exit(console)
    chosen = _resolve_provider(provider or None, non_interactive=non_interactive)
    var = KEY_VAR[chosen]
    existing = _existing_key(paths.env_file, var)
    new_key = _resolve_key(
        chosen,
        arg=key or None,
        non_interactive=non_interactive,
        rotating=rotate,
        existing=existing,
    )
    if not new_key:
        console.print("[red]Empty key; nothing to do.[/red]")
        raise typer.Exit(code=1)

    if not no_validate:
        console.print(f"Validating {var} against {chosen}...")
        ok, err = asyncio.run(_validate(chosen, new_key))
        if not ok:
            console.print(f"[red]Validation failed:[/red] {err}")
            console.print(
                "[dim]Pass --no-validate to write the key anyway "
                "(useful for offline or rate-limited setups).[/dim]"
            )
            raise typer.Exit(code=1)
        console.print("[green]✓[/green] key validated")

    _ensure_env_file(paths.env_file)
    set_key(str(paths.env_file), var, new_key, quote_mode="never")
    # Re-apply 0600 in case set_key recreated the file with default perms.
    try:
        paths.env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    masked = new_key[:4] + "..." + new_key[-4:] if len(new_key) > 8 else "***"
    rel = paths.env_file
    try:
        rel = paths.env_file.relative_to(paths.root)
    except ValueError:
        pass
    verb = "rotated" if existing else "saved"
    console.print(f"[green]✓[/green] {verb} {var}={masked} to {rel} (mode 0600)")
    console.print()
    console.print("Next steps:")
    console.print(
        "  1. [cyan]devrel doctor[/cyan]                    confirm everything is wired up"
    )
    console.print(
        '  2. [cyan]devrel content draft "..."[/cyan]      ship your first grounded draft'
    )
    if chosen == PROVIDER_ANTHROPIC:
        console.print(
            "[dim]Tip: switch providers with `devrel auth --provider openrouter` "
            "(free credits at https://openrouter.ai/).[/dim]"
        )

    # Make the key visible in the current process env too, in case the user
    # immediately runs another devrel verb in the same shell pipeline.
    os.environ[var] = new_key
