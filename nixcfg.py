#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

import sys
from typing import Protocol, cast

import typer
from rich.console import Console
from rich.tree import Tree
from typer.main import get_command

from lib.cli import HELP_CONTEXT_SETTINGS, make_lazy_typer_app

_is_tty = sys.stdout.isatty()


class _Command(Protocol):
    """Minimal Click/Typer command surface used for command-tree rendering."""

    hidden: bool
    help: str | None
    short_help: str | None

    def get_short_help_str(self) -> str: ...


app = typer.Typer(
    name="nixcfg",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
    context_settings=dict(HELP_CONTEXT_SETTINGS),
)

app.add_typer(
    make_lazy_typer_app(
        module_name="lib.github_actions.cli",
        help_text="GitHub Actions workflow discovery and live-tail helpers.",
    ),
    name="actions",
)
app.add_typer(
    make_lazy_typer_app(
        module_name="lib.update.ci",
        help_text="Package-maintenance tools.",
    ),
    name="ci",
)
app.add_typer(
    make_lazy_typer_app(
        module_name="lib.recover.cli",
        help_text="Recover tracked files from realised generation snapshots.",
    ),
    name="recover",
)


app.add_typer(
    make_lazy_typer_app(
        module_name="lib.schema_codegen.cli",
        help_text="Nix JSON schema utilities (fetch, codegen).",
    ),
    name="schema",
)


def _command_description(command: _Command) -> str:
    """Return a single-line description from declared command help text."""
    description = command.help or command.short_help or command.get_short_help_str()
    return " ".join((description or "").split())


def _command_children(command: _Command) -> dict[str, _Command] | None:
    """Return declared child commands for Click command groups."""
    commands = getattr(command, "commands", None)
    if not isinstance(commands, dict):
        return None
    return cast("dict[str, _Command]", commands)


def _has_visible_subcommands(command: _Command) -> bool:
    """Return whether a command group exposes any non-hidden child commands."""
    commands = _command_children(command)
    if commands is None:
        return False
    return any(not subcommand.hidden for subcommand in commands.values())


def _command_label(name: str, command: _Command) -> str:
    """Return a styled label for one command in the tree output."""
    style = "bold cyan" if _has_visible_subcommands(command) else "green"
    description = _command_description(command)
    if description:
        return f"[{style}]{name}[/{style}] [dim]- {description}[/dim]"
    return f"[{style}]{name}[/{style}]"


def _add_command_nodes(tree: Tree, group: _Command) -> None:
    """Append child command nodes recursively in alphabetical order."""
    commands = _command_children(group)
    if commands is None:
        return
    for name in sorted(commands):
        command = commands[name]
        if command.hidden:
            continue
        child = tree.add(_command_label(name, command))
        if _command_children(command) is not None:
            _add_command_nodes(child, command)


@app.command(name="tree", help="Show the full command tree.")
def command_tree() -> None:
    """Render all available commands as a Rich tree."""
    root = get_command(app)
    if not hasattr(root, "commands"):
        typer.echo("nixcfg")
        return

    tree = Tree("[bold magenta]nixcfg[/bold magenta]")
    _add_command_nodes(tree, root)
    Console().print(tree)


app.add_typer(
    make_lazy_typer_app(
        module_name="lib.update.cli",
        help_text="Update source versions/hashes and flake input refs.",
    ),
    name="update",
)


def main() -> None:
    """Run the CLI with a stable program name for help output."""
    app(prog_name="nixcfg")


if __name__ == "__main__":
    main()
