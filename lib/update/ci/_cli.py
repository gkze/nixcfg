"""Shared Typer helpers for CI command modules."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click
import typer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

from lib.cli import HELP_CONTEXT_SETTINGS


def make_typer_app(*, help_text: str, no_args_is_help: bool = False) -> typer.Typer:
    """Create a CI Typer app with consistent defaults."""
    return typer.Typer(
        help=help_text,
        add_completion=False,
        no_args_is_help=no_args_is_help,
        context_settings=HELP_CONTEXT_SETTINGS,
    )


@dataclass(frozen=True)
class DualTyperApps:
    """Paired Typer apps for mounted and standalone invocation modes."""

    app: typer.Typer
    standalone_app: typer.Typer


def make_dual_typer_apps(
    *,
    help_text: str,
    no_args_is_help: bool = False,
) -> DualTyperApps:
    """Create paired Typer apps for callback + standalone command wiring."""
    return DualTyperApps(
        app=make_typer_app(help_text=help_text, no_args_is_help=no_args_is_help),
        standalone_app=make_typer_app(
            help_text=help_text,
            no_args_is_help=no_args_is_help,
        ),
    )


def register_dual_entrypoint(
    dual_apps: DualTyperApps,
    *,
    invoke_without_command: bool = True,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Register one function as both mounted callback and standalone command."""

    def _decorator(func: Callable[..., object]) -> Callable[..., object]:
        callback = dual_apps.app.callback(invoke_without_command=invoke_without_command)
        command = dual_apps.standalone_app.command()
        return command(callback(func))

    return _decorator


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


def make_main(
    app: typer.Typer,
    *,
    prog_name: str,
    default_exit_code: int = 0,
) -> Callable[..., int]:
    """Build a conventional ``main(argv)`` wrapper for a Typer app."""

    def _main(argv: Sequence[str] | None = None) -> int:
        return run_main(
            app,
            argv=argv,
            prog_name=prog_name,
            default_exit_code=default_exit_code,
        )

    return _main
