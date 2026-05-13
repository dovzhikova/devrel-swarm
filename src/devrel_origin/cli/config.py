"""`devrel config {get, set}` — read/write .devrel/config.toml values.

Dotted keys (e.g. `budget.monthly_usd`) navigate nested tables. `set`
performs naive type coercion: int / float / "true"|"false" / fallback to
string. The TOML file is round-tripped via tomllib (read) + tomli_w
(write); comments are not preserved (acceptable for a setter).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit

console = Console()

config_app = typer.Typer(
    name="config",
    help="Read and write .devrel/config.toml values by dotted key.",
    no_args_is_help=True,
    add_completion=False,
)


def _coerce(value: str) -> Any:
    """Coerce a string from the CLI into int / float / bool / str."""
    s = value.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    # int (no leading sign issues, no underscore, no decimal point)
    try:
        if s and (s[0] in "+-" or s[0].isdigit()):
            if "." not in s and "e" not in s.lower():
                return int(s)
            return float(s)
    except ValueError:
        pass
    return s


def _get_nested(d: dict, key: str) -> Any:
    cur: Any = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(key)
        cur = cur[part]
    return cur


def _set_nested(d: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def _load(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _dump(path: Path, data: dict) -> None:
    with path.open("wb") as f:
        tomli_w.dump(data, f)


@config_app.command("get")
def get_value(
    key: str = typer.Argument(..., help="Dotted key (e.g. 'project.name', 'budget.monthly_usd')."),
) -> None:
    """Read a config value."""
    paths = find_paths_or_exit(console)
    data = _load(paths.config_file)
    try:
        val = _get_nested(data, key)
    except KeyError:
        console.print(f"[red]Key not found: {key}[/red]")
        raise typer.Exit(code=1) from None
    if isinstance(val, (dict, list)):
        typer.echo(json.dumps(val, indent=2))
    else:
        typer.echo(str(val))


@config_app.command("set")
def set_value(
    key: str = typer.Argument(..., help="Dotted key to set."),
    value: str = typer.Argument(..., help="Value (int/float/true/false/string auto-detected)."),
) -> None:
    """Write a config value (round-trips via tomli_w; comments not preserved)."""
    paths = find_paths_or_exit(console)
    data = _load(paths.config_file)
    coerced = _coerce(value)
    _set_nested(data, key, coerced)
    _dump(paths.config_file, data)
    console.print(f"[green]✓[/green] {key} = {coerced!r}  ({type(coerced).__name__})")
