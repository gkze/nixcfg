#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import pathlib  # noqa: TC003
import sys
from typing import Annotated

import click
import httpx
import typer
from rich.console import Console
from rich.tree import Tree
from typer.main import get_command

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.nix.schemas._codegen import main as codegen_main
from lib.nix.schemas._fetch import check as schema_check
from lib.nix.schemas._fetch import fetch as fetch_schemas
from lib.recover.cli import app as recover_app
from lib.schema_codegen import (
    DEFAULT_CONFIG_PATH,
    generate_schema_codegen_target,
    list_schema_codegen_targets,
    write_codegen_lockfile,
)
from lib.update.ci import app as update_ci_app
from lib.update.cli import app as update_app
from lib.update.paths import get_repo_root

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


def _display_schema_path(path: pathlib.Path) -> str:
    """Return a readable display path for schema-related outputs."""
    try:
        return str(path.relative_to(get_repo_root()))
    except ValueError:
        return str(path)


@schema_app.command(
    name="targets",
    help="List declarative schema codegen targets.",
)
def schema_targets(
    *,
    config: Annotated[
        pathlib.Path,
        typer.Option(
            "-c",
            "--config",
            help="Path to the schema codegen config file.",
        ),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """List available schema generation targets from the declarative config."""
    try:
        for target in list_schema_codegen_targets(config_path=config):
            typer.echo(f"{target.name}\t{_display_schema_path(target.output)}")
    except (FileNotFoundError, RuntimeError, TypeError, ValueError) as exc:
        typer.echo(f"Schema target listing failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@schema_app.command(
    name="generate",
    help="Generate models for one declarative schema codegen target.",
)
def schema_generate(
    target: Annotated[
        str,
        typer.Argument(help="Name of the configured generation target."),
    ],
    *,
    config: Annotated[
        pathlib.Path,
        typer.Option(
            "-c",
            "--config",
            help="Path to the schema codegen config file.",
        ),
    ] = DEFAULT_CONFIG_PATH,
) -> None:
    """Run the declarative schema codegen pipeline for one target."""
    try:
        output_path = generate_schema_codegen_target(
            config_path=config,
            progress=_schema_progress,
            target_name=target,
        )
    except (FileNotFoundError, RuntimeError, TypeError, ValueError) as exc:
        typer.echo(f"Schema generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Generated {_display_schema_path(output_path)}")


@schema_app.command(
    name="lock",
    help="Materialize a canonical codegen lockfile from a v1 manifest.",
)
def schema_lock(
    manifest: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to the canonical codegen manifest (YAML or JSON)."),
    ],
    *,
    output: Annotated[
        pathlib.Path | None,
        typer.Option(
            "-o",
            "--output",
            help="Path to write the lockfile. Defaults to codegen.lock.json next to the manifest.",
        ),
    ] = None,
    include_metadata: Annotated[
        bool,
        typer.Option(
            "-m",
            "--include-metadata",
            help="Include non-semantic timestamps and provenance metadata.",
        ),
    ] = False,
) -> None:
    """Write a deterministic lockfile for the canonical codegen manifest schema."""
    try:
        output_path = write_codegen_lockfile(
            manifest_path=manifest,
            lockfile_path=output,
            include_metadata=include_metadata,
            progress=_schema_progress,
        )
    except (
        FileNotFoundError,
        RuntimeError,
        TypeError,
        ValueError,
        httpx.HTTPError,
    ) as exc:
        typer.echo(f"Schema lockfile generation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Generated {_display_schema_path(output_path)}")


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
