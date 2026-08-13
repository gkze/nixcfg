"""Unified Typer app for package-maintenance tooling."""

from __future__ import annotations

import typer

from lib.cli import HELP_CONTEXT_SETTINGS, make_main
from lib.update.ci.bun_lock import app as bun_lock_app
from lib.update.ci.crate2nix import app as crate2nix_app

app = typer.Typer(
    name="ci",
    help="Package-maintenance tools.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)

pipeline_app = typer.Typer(
    help="Package artifact maintenance tools.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)

app.add_typer(pipeline_app, name="pipeline")

pipeline_app.add_typer(crate2nix_app, name="crate2nix")
pipeline_app.add_typer(bun_lock_app, name="bun-lock")


main = make_main(app, prog_name="nixcfg ci")
