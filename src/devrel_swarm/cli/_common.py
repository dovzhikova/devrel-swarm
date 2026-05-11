"""Shared CLI helpers."""

from __future__ import annotations

import json
import os
import tomllib
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console

from devrel_swarm.core.agent_config import AgentConfig
from devrel_swarm.core.atlas import Atlas, DelegationResult
from devrel_swarm.core.llm import LLMClient
from devrel_swarm.project.paths import ProjectNotFoundError, ProjectPaths, find_devrel_root
from devrel_swarm.tools.api_client import PostHogClient
from devrel_swarm.tools.apollo_client import ApolloClient
from devrel_swarm.tools.github_tools import GitHubTools
from devrel_swarm.tools.instantly_client import InstantlyClient
from devrel_swarm.tools.search_tools import SearchTools


def find_paths_or_exit(console: Console) -> ProjectPaths:
    try:
        return ProjectPaths.from_root(find_devrel_root())
    except ProjectNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


def _load_project_env(paths: ProjectPaths) -> None:
    """Pull keys from .devrel/.env (preferred) and project-root .env (fallback)
    into the process env, without overriding values the user has already
    exported in their shell. Called at the top of build paths so `devrel run`
    works even when the user hasn't `export`ed anything.

    `override=False` is intentional: shell-exported values take precedence so
    one-off overrides during debugging don't get clobbered by a stale file.
    Both paths are best-effort; missing files are silent.
    """
    if paths.env_file.is_file():
        load_dotenv(paths.env_file, override=False)
    root_env = paths.root / ".env"
    if root_env.is_file() and root_env != paths.env_file:
        load_dotenv(root_env, override=False)


def _read_project_toml(paths: ProjectPaths) -> dict[str, Any]:
    """Parse .devrel/config.toml or return {} on missing/malformed."""
    if not paths.config_file.is_file():
        return {}
    try:
        with paths.config_file.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _load_agent_config(paths: ProjectPaths) -> AgentConfig:
    """Bridge .devrel/config.toml into Atlas's AgentConfig.

    Reads [project] for product_name/product_url and [orchestration] for
    agent_timeouts/cro_in_run/analytics_in_run. Other AgentConfig fields stay
    at defaults (the legacy YAML loader at config/agent_config.yaml is not
    wired into the per-project CLI; this is the bridge that makes
    .devrel/config.toml settings actually take effect on `devrel run`).
    """
    raw = _read_project_toml(paths)
    proj = raw.get("project") or {}
    orch = raw.get("orchestration") or {}

    kwargs: dict[str, Any] = {}
    if proj.get("name"):
        kwargs["product_name"] = str(proj["name"])
    if proj.get("url"):
        kwargs["product_url"] = str(proj["url"])
    if "analytics_in_run" in orch:
        kwargs["analytics_in_run"] = bool(orch["analytics_in_run"])
    if "cro_in_run" in orch:
        kwargs["cro_in_run"] = bool(orch["cro_in_run"])
    timeouts = orch.get("agent_timeouts") or {}
    if timeouts:
        kwargs["agent_timeouts"] = {k: float(v) for k, v in timeouts.items()}
    return AgentConfig(**kwargs)


def _resolve_github_repo(paths: ProjectPaths) -> str:
    """Pick the GitHub repo Sage/etc should target.

    Order: GITHUB_REPO env > [project].github_repo in .devrel/config.toml >
    empty string (GitHubTools falls back to its DEFAULT_REPO).
    """
    env = os.environ.get("GITHUB_REPO", "").strip()
    if env:
        return env
    raw = _read_project_toml(paths)
    proj = raw.get("project") or {}
    repo = proj.get("github_repo")
    return str(repo).strip() if repo else ""


_MISSING_KEY_HELP = (
    "[red]No LLM API key found.[/red]\n"
    "Fix:\n"
    "  - Run [bold]devrel auth[/bold] to configure interactively (writes "
    ".devrel/.env with chmod 600).\n"
    "  - Or set [bold]ANTHROPIC_API_KEY[/bold] in your shell.\n"
    "  - Or set [bold]OPENROUTER_API_KEY[/bold] for multi-provider routing "
    "(free credits at https://openrouter.ai/)."
)


def _build_llm_client(paths: ProjectPaths, console: Console) -> LLMClient:
    """Construct an LLMClient honoring provider + per-agent overrides from
    .devrel/config.toml's [llm] section, with env-var fallback.

    Provider resolution:
      1. [llm].provider explicitly set ('anthropic' | 'openrouter')
      2. OPENROUTER_API_KEY set without ANTHROPIC_API_KEY -> openrouter
      3. Default -> anthropic

    Either ANTHROPIC_API_KEY or OPENROUTER_API_KEY (matching the chosen
    provider) must be present; otherwise we exit with a clear message
    pointing at `devrel auth`. Pulls keys from .devrel/.env or root .env
    before reading the env so users don't have to `export` anything.
    """
    _load_project_env(paths)
    raw = _read_project_toml(paths)
    llm_cfg = raw.get("llm") or {}
    provider = (llm_cfg.get("provider") or "").strip().lower() or None
    agent_models_raw = llm_cfg.get("agent_models") or {}
    agent_models = {str(k): str(v) for k, v in agent_models_raw.items()}

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()

    # Decide which provider we'll actually use, matching make_backend's logic
    # so we can validate the key presence before we even construct a client.
    if provider == "openrouter" or (provider is None and openrouter_key and not anthropic_key):
        if not openrouter_key:
            console.print(
                '[red]OPENROUTER_API_KEY is required when [llm].provider = "openrouter".[/red]'
            )
            console.print(_MISSING_KEY_HELP)
            raise typer.Exit(code=1)
        return LLMClient(
            provider="openrouter",
            openrouter_api_key=openrouter_key,
            agent_models=agent_models,
        )

    if not anthropic_key:
        console.print(_MISSING_KEY_HELP)
        raise typer.Exit(code=1)
    return LLMClient(
        provider="anthropic",
        api_key=anthropic_key,
        agent_models=agent_models,
    )


def build_atlas_or_exit(paths: ProjectPaths, console: Console) -> Atlas:
    llm = _build_llm_client(paths, console)
    posthog = PostHogClient(
        api_key=os.environ.get("POSTHOG_API_KEY", ""),
        project_id=os.environ.get("POSTHOG_PROJECT_ID", ""),
    )

    # Optional integrations: only construct when the relevant key is present so
    # specialists fall back to their degraded-mode paths instead of crashing on
    # init. Wiring is the regression that left agents (Sage / Echo / Rex / Vox /
    # Pax / Mox) in their no-tool branches even when keys were configured.
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo = _resolve_github_repo(paths)
    if github_repo:
        # Public GitHub repositories can be read unauthenticated. Constructing
        # GitHubTools with an empty token still lets Sage/Rex/Argus use the
        # configured repo instead of silently falling back to no-tool mode.
        github_tools = GitHubTools(token=github_token, repo=github_repo)
    elif github_token:
        github_tools = GitHubTools(token=github_token)
    else:
        github_tools = None

    if (
        os.environ.get("FIRECRAWL_API_KEY", "").strip()
        or os.environ.get("BRAVE_API_KEY", "").strip()
    ):
        search_tools = SearchTools(
            firecrawl_api_key=os.environ.get("FIRECRAWL_API_KEY", ""),
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
        )
    else:
        search_tools = None

    instantly_key = os.environ.get("INSTANTLY_API_KEY", "").strip()
    instantly_client = InstantlyClient(api_key=instantly_key) if instantly_key else None

    apollo_key = os.environ.get("APOLLO_API_KEY", "").strip()
    apollo_client = ApolloClient(api_key=apollo_key) if apollo_key else None

    return Atlas(
        api_client=posthog,
        knowledge_base_path=paths.kb_dir,
        archive_dir=paths.context_dir,
        llm_client=llm,
        github_tools=github_tools,
        search_tools=search_tools,
        config=_load_agent_config(paths),
        instantly_client=instantly_client,
        apollo_client=apollo_client,
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
