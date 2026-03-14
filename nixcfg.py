#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import sys
from typing import Annotated

import click
import typer
from rich.console import Console
from rich.tree import Tree
from typer.main import get_command

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.nix.schemas._codegen import main as codegen_main
from lib.nix.schemas._fetch import check as schema_check
from lib.nix.schemas._fetch import fetch as fetch_schemas
from lib.recover.cli import app as recover_app
from lib.update.ci import app as update_ci_app
from lib.update.cli import app as update_app

_is_tty = sys.stdout.isatty()

app = typer.Typer(
    name="nixcfg",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
    context_settings=dict(HELP_CONTEXT_SETTINGS),
)

app.add_typer(update_ci_app, name="ci")
app.add_typer(recover_app, name="recover")


schema_app = typer.Typer(
    name="schema",
    help="Nix JSON schema utilities (fetch, codegen).",
    no_args_is_help=True,
    rich_markup_mode="rich" if _is_tty else None,
    context_settings=dict(HELP_CONTEXT_SETTINGS),
)
app.add_typer(schema_app)


def _command_description(command: click.Command) -> str:
    """Return a single-line description from declared command help text."""
    description = command.help or command.short_help or command.get_short_help_str()
    return " ".join((description or "").split())


def _has_visible_subcommands(command: click.Command) -> bool:
    """Return whether a command group exposes any non-hidden child commands."""
    if not isinstance(command, click.Group):
        return False
    return any(not subcommand.hidden for subcommand in command.commands.values())


def _command_label(name: str, command: click.Command) -> str:
    """Return a styled label for one command in the tree output."""
    style = "bold cyan" if _has_visible_subcommands(command) else "green"
    description = _command_description(command)
    if description:
        return f"[{style}]{name}[/{style}] [dim]- {description}[/dim]"
    return f"[{style}]{name}[/{style}]"


def _add_command_nodes(tree: Tree, group: click.Group) -> None:
    """Append child command nodes recursively in alphabetical order."""
    for name in sorted(group.commands):
        command = group.commands[name]
        if command.hidden:
            continue
        child = tree.add(_command_label(name, command))
        if isinstance(command, click.Group):
            _add_command_nodes(child, command)


@app.command(name="tree", help="Show the full command tree.")
def command_tree() -> None:
    """Render all available commands as a Rich tree."""
    root = get_command(app)
    if not isinstance(root, click.Group):
        typer.echo("nixcfg")
        return

    tree = Tree("[bold magenta]nixcfg[/bold magenta]")
    _add_command_nodes(tree, root)
    Console().print(tree)


app.add_typer(update_app, name="update")


def _schema_progress(message: str) -> None:
    """Render schema command progress updates to stderr."""
    typer.echo(message, err=True)


@schema_app.command(
    name="codegen",
    help="Generate Pydantic models from vendored Nix schemas.",
)
def schema_codegen() -> None:
    """Run the Pydantic model code generator."""
    codegen_main(progress=_schema_progress)


@schema_app.command(
    name="fetch",
    help="Fetch Nix JSON schemas from the NixOS/nix repo.",
)
def schema_fetch(
    *,
    check: Annotated[
        bool,
        typer.Option(
            "-c",
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


def main() -> None:
    """Run the CLI with a stable program name for help output."""
    app(prog_name="nixcfg")


if __name__ == "__main__":
    main()
