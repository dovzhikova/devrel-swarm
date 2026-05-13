"""Project path discovery and structure.

`find_devrel_root` walks up from a starting directory looking for the nearest
ancestor containing a `.devrel/config.toml`. `ProjectPaths` is a frozen
dataclass holding every derived path under `.devrel/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEVREL_DIR_NAME = ".devrel"
CONFIG_FILE_NAME = "config.toml"


class ProjectNotFoundError(Exception):
    """Raised when no .devrel/config.toml is found in cwd or any ancestor."""


@dataclass(frozen=True)
class ProjectPaths:
    """All derived paths for a devrel-origin project."""

    root: Path
    devrel_dir: Path
    config_file: Path
    voice_file: Path
    style_file: Path
    slop_file: Path
    kb_dir: Path
    deliverables_dir: Path
    context_dir: Path
    state_db: Path
    env_file: Path
    gitignore: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        d = root / DEVREL_DIR_NAME
        return cls(
            root=root,
            devrel_dir=d,
            config_file=d / CONFIG_FILE_NAME,
            voice_file=d / "voice.md",
            style_file=d / "style.md",
            slop_file=d / "slop-blocklist.md",
            kb_dir=d / "kb",
            deliverables_dir=d / "deliverables",
            context_dir=d / "context",
            state_db=d / "state.db",
            env_file=d / ".env",
            gitignore=d / ".gitignore",
        )


def find_devrel_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) until a `.devrel/config.toml` is
    found. Returns the project root (parent of `.devrel/`), resolved to an
    absolute path.

    Raises ProjectNotFoundError if no `.devrel/config.toml` is found before
    the filesystem root.
    """
    cur = (start if start is not None else Path.cwd()).resolve()
    while True:
        candidate = cur / DEVREL_DIR_NAME / CONFIG_FILE_NAME
        if candidate.is_file():
            return cur
        if cur.parent == cur:
            raise ProjectNotFoundError(
                "No .devrel/config.toml found in cwd or any ancestor. "
                "Run `devrel init` from the project root."
            )
        cur = cur.parent
