"""Shared Typer helpers for CI command modules."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click
import typer

if TYPE_CHECKING:
    from collections.abc import Sequence


def make_typer_app(*, help_text: str, no_args_is_help: bool = False) -> typer.Typer:
    """Create a CI Typer app with consistent defaults."""
    return typer.Typer(
        help=help_text,
        add_completion=False,
        no_args_is_help=no_args_is_help,
    )


def run_main(
    app: typer.Typer,
    *,
    argv: Sequence[str] | None,
    prog_name: str,
    default_exit_code: int = 0,
) -> int:
    """Run a Typer app and normalize Click/Typer exits to int codes."""
    args = list(argv) if argv is not None else None
    try:
        result = app(args=args, prog_name=prog_name, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.exceptions.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(exc.exit_code)
    return int(result) if isinstance(result, int) else default_exit_code
