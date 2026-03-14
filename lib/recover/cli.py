"""Typer CLI for recovery from realised generation snapshots."""

from __future__ import annotations

from typing import Annotated

import typer

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.recover.files import run_file_recovery
from lib.recover.hashes import run_hash_recovery
from lib.recover.snapshot import DEFAULT_GENERATION, run_snapshot_recovery

app = typer.Typer(
    help="Recover tracked files from realised generation snapshots.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@app.command("snapshot", help="Print the source snapshot for a generation.")
def recover_snapshot(
    generation: Annotated[
        str,
        typer.Argument(help="Generation or realised path to inspect."),
    ] = DEFAULT_GENERATION,
    *,
    json_output: Annotated[
        bool,
        typer.Option("-j", "--json", help="Emit machine-readable JSON output."),
    ] = False,
) -> None:
    """Print the resolved source snapshot for a realised generation."""
    raise typer.Exit(code=run_snapshot_recovery(generation, json_output=json_output))


@app.command("files", help="Recover selected repo files from a generation snapshot.")
def recover_files(
    generation: Annotated[
        str,
        typer.Argument(help="Generation or realised path to recover from."),
    ] = DEFAULT_GENERATION,
    *,
    apply: Annotated[
        bool,
        typer.Option("-a", "--apply", help="Write recovered files into the repo."),
    ] = False,
    globs: Annotated[
        list[str] | None,
        typer.Option(
            "-G",
            "--glob",
            help="Repo-relative glob to recover. Repeat as needed.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("-j", "--json", help="Emit machine-readable JSON output."),
    ] = False,
    paths: Annotated[
        list[str] | None,
        typer.Option(
            "-p",
            "--path",
            help="Repo-relative file path to recover. Repeat as needed.",
        ),
    ] = None,
    stage: Annotated[
        bool,
        typer.Option("-g", "--stage", help="Stage recovered file changes in Git."),
    ] = False,
    sync: Annotated[
        bool,
        typer.Option(
            "-s",
            "--sync",
            help="Also remove selected files missing from the recovered snapshot.",
        ),
    ] = False,
) -> None:
    """Recover selected repo files from a realised generation snapshot."""
    raise typer.Exit(
        code=run_file_recovery(
            generation,
            apply=apply,
            globs=tuple(globs or ()),
            json_output=json_output,
            paths=tuple(paths or ()),
            stage=stage,
            sync=sync,
        )
    )


@app.command("hashes", help="Recover flake.lock and sources.json from a generation.")
def recover_hashes(
    generation: Annotated[
        str,
        typer.Argument(help="Generation or realised path to recover from."),
    ] = DEFAULT_GENERATION,
    *,
    apply: Annotated[
        bool,
        typer.Option("-a", "--apply", help="Write recovered files into the repo."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("-j", "--json", help="Emit machine-readable JSON output."),
    ] = False,
    stage: Annotated[
        bool,
        typer.Option("-g", "--stage", help="Stage recovered file changes in Git."),
    ] = False,
    sync: Annotated[
        bool,
        typer.Option(
            "-s",
            "--sync",
            help="Also remove managed files missing from the recovered snapshot.",
        ),
    ] = False,
) -> None:
    """Recover flake.lock and sources.json from a realised generation."""
    raise typer.Exit(
        code=run_hash_recovery(
            generation,
            apply=apply,
            json_output=json_output,
            stage=stage,
            sync=sync,
        )
    )
