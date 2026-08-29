"""CLI adapter for checked-in Bun lock maintenance."""

import pathlib
from typing import Annotated

import typer

from lib.cli import make_main, make_typer_app
from lib.update.bun_lock import prepare_source_package_lock

app = make_typer_app(
    help_text="Validate or repair checked-in Bun source-package locks.",
    no_args_is_help=True,
)


@app.command(name="prepare")
def prepare_cli(
    *,
    workspace_root: Annotated[
        pathlib.Path,
        typer.Option("-w", "--workspace-root", help="Workspace used for relocking."),
    ],
    lock_file: Annotated[
        pathlib.Path,
        typer.Option("-l", "--lock-file", help="Bun lock to validate or relock."),
    ] = pathlib.Path("bun.lock"),
    bun_executable: Annotated[
        str,
        typer.Option("-b", "--bun-executable", help="Bun executable to run."),
    ] = "bun",
) -> None:
    """Validate a Bun lock and relock when source overrides disagree."""
    try:
        relocked = prepare_source_package_lock(
            workspace_root,
            lock_file,
            bun_executable=bun_executable,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    action = "Relocked" if relocked else "Validated"
    typer.echo(f"{action} Bun source package overrides for {lock_file}")


main = make_main(app, prog_name="pipeline bun-lock")


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    raise SystemExit(main())


__all__ = ["app", "main"]
