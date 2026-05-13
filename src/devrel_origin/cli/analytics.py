"""Deprecated alias. Use `devrel argus ...`. Retained until v1.0 for backward compat."""

import warnings

import typer

from devrel_origin.cli.argus import argus_app

analytics_app = typer.Typer(
    name="analytics",
    help="DEPRECATED: use `devrel argus` instead. Forwarding to argus...",
    invoke_without_command=False,
)


@analytics_app.callback()
def _deprecation_notice() -> None:
    warnings.warn(
        "`devrel analytics ...` is deprecated; use `devrel argus ...` instead. "
        "The alias will be removed in v1.0.",
        DeprecationWarning,
        stacklevel=2,
    )


# Forward all subcommands from argus_app
for cmd in argus_app.registered_commands:
    analytics_app.registered_commands.append(cmd)
