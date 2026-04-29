"""Idempotent .devrel/ scaffolder.

`init_project(root, opts)` writes the .devrel/ directory tree, copies the
template files, substitutes config placeholders, and initializes the state
DB. Re-running on an existing project preserves user edits to committed
files (config.toml, voice.md, style.md, slop-blocklist.md, .gitignore) —
those are listed in `result.skipped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from devrel_swarm.project.paths import ProjectPaths
from devrel_swarm.project.state import init_db

_TEMPLATE_PKG = "devrel_swarm.project.templates"

# Files that are committed and must NEVER be overwritten on re-init.
_COMMITTED_FILES = ("config.toml", "voice.md", "style.md", "slop-blocklist.md", ".gitignore")


@dataclass(frozen=True)
class InitOptions:
    name: str
    url: str = ""
    github_repo: str | None = None
    dry_run: bool = False


@dataclass
class InitResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)
    dry_run: bool = False


def _read_template(name: str) -> str:
    return (files(_TEMPLATE_PKG) / name).read_text(encoding="utf-8")


def _render_config_toml(opts: InitOptions) -> str:
    body = _read_template("config.toml")
    body = body.replace("PROJECT_NAME", opts.name)
    body = body.replace("PROJECT_URL", opts.url)
    if opts.github_repo:
        body = body.replace('github_repo = "OWNER/REPO"', f'github_repo = "{opts.github_repo}"')
    else:
        body = body.replace(
            'github_repo = "OWNER/REPO"',
            "# github_repo =   # set if this product has a public repo",
        )
    return body


def init_project(root: Path, opts: InitOptions) -> InitResult:
    """Scaffold .devrel/ under `root`. Idempotent: preserves committed files
    on re-run."""
    paths = ProjectPaths.from_root(root)
    result = InitResult(dry_run=opts.dry_run)

    # The directory and subdirectories.
    dirs = [paths.devrel_dir, paths.kb_dir, paths.deliverables_dir, paths.context_dir]
    for d in dirs:
        if d.is_dir():
            result.skipped.append(d.name + "/")
        else:
            if opts.dry_run:
                result.would_create.append(d.name + "/")
            else:
                d.mkdir(parents=True, exist_ok=True)
                result.created.append(d.name + "/")

    # File payloads keyed by destination path.
    payloads: dict[Path, str] = {
        paths.config_file: _render_config_toml(opts),
        paths.voice_file: _read_template("voice.md"),
        paths.style_file: _read_template("style.md"),
        paths.slop_file: _read_template("slop-blocklist.md"),
        paths.gitignore: _read_template("devrel.gitignore"),
    }
    for dest, body in payloads.items():
        if dest.is_file():
            result.skipped.append(dest.name)
            continue
        if opts.dry_run:
            result.would_create.append(dest.name)
        else:
            dest.write_text(body, encoding="utf-8")
            result.created.append(dest.name)

    # State DB: idempotent (init_db preserves rows).
    if opts.dry_run:
        if not paths.state_db.is_file():
            result.would_create.append("state.db")
    else:
        already = paths.state_db.is_file()
        init_db(paths.state_db)
        (result.skipped if already else result.created).append("state.db")

    return result
