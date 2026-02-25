#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from lib.nix.schemas._codegen import main as codegen_main
from lib.nix.schemas._fetch import check as schema_check
from lib.nix.schemas._fetch import fetch as fetch_schemas
from lib.update.ci import app as update_ci_app
from lib.update.cli import cli as update_cli

_is_tty = sys.stdout.isatty()

app = typer.Typer(
    name="nixcfg",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
)

app.add_typer(update_ci_app, name="ci")

app.command(
    name="update",
    help="Update source versions/hashes and flake input refs.",
)(update_cli)


schema_app = typer.Typer(
    name="schema",
    help="Nix JSON schema utilities (fetch, codegen).",
    no_args_is_help=True,
    rich_markup_mode="rich" if _is_tty else None,
)
app.add_typer(schema_app)


def _schema_progress(message: str) -> None:
    """Render schema command progress updates to stderr."""
    typer.echo(message, err=True)


@schema_app.command(
    name="fetch",
    help="Fetch Nix JSON schemas from the NixOS/nix repo.",
)
def schema_fetch(
    *,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Verify vendored schemas match the pinned commit.",
        ),
    ] = False,
) -> None:
    """Download or verify vendored Nix JSON schemas."""
    try:
        if check:
            ok = schema_check()
            raise typer.Exit(code=0 if ok else 1)
        fetch_schemas(progress=_schema_progress)
    except RuntimeError as exc:
        typer.echo(f"Schema fetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@schema_app.command(
    name="codegen",
    help="Generate Pydantic models from vendored Nix schemas.",
)
def schema_codegen() -> None:
    """Run the Pydantic model code generator."""
    codegen_main(progress=_schema_progress)


def main() -> None:
    """Run the CLI with a stable program name for help output."""
    app(prog_name="nixcfg")


if __name__ == "__main__":
    main()
