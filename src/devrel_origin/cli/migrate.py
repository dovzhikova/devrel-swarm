"""`devrel migrate` - upgrade the project state.db schema in place.

State DBs created by older devrel-origin releases (e.g. 0.2.4 / schema v4)
must be upgraded to the current SCHEMA_VERSION before agents can write to
the new tables. `init_db()` is idempotent and runs the v5 migration on any
existing DB; this verb just exposes it as a discoverable CLI command so
users don't have to import internals.
"""

from __future__ import annotations

import typer
from rich.console import Console

from devrel_origin.cli._common import find_paths_or_exit
from devrel_origin.project.state import SCHEMA_VERSION, get_schema_version, init_db

console = Console()


def migrate_command() -> None:
    """Upgrade the project state.db schema to the current version."""
    paths = find_paths_or_exit(console)
    db = paths.state_db

    before = get_schema_version(db) if db.is_file() else None
    if before == SCHEMA_VERSION:
        console.print(
            f"[green]✓[/green] state.db already at schema v{SCHEMA_VERSION}; nothing to migrate."
        )
        return

    init_db(db)
    after = get_schema_version(db)

    if after != SCHEMA_VERSION:
        console.print(
            f"[red]✗[/red] migration ran but schema version is v{after}, expected v{SCHEMA_VERSION}."
        )
        raise typer.Exit(code=1)

    if before is None:
        console.print(f"[green]✓[/green] state.db created at schema v{after}.")
    else:
        console.print(f"[green]✓[/green] state.db migrated v{before} -> v{after}.")
