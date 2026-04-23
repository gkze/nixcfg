"""CLI wrapper for shared crate2nix regeneration logic."""

from __future__ import annotations

from typing import Annotated

import typer

from lib.update.ci._cli import make_main, make_typer_app
from lib.update.crate2nix import (
    REPO_ROOT,
    TARGETS,
    Crate2NixTarget,
    RefreshResult,
    _current_platform,
    _normalize_json_text,
    _normalize_trailing_newline,
    _refresh_target,
    _resolve_targets,
    _stabilize_generated_command_comment,
    _stabilize_generated_root_src_paths,
    _target_has_changes,
    _write_target,
    crate2nix_artifact_updates,
    run,
    stream_crate2nix_artifact_updates,
)

app = make_typer_app(
    help_text="Check or refresh checked-in crate2nix artifacts.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def cli(
    *,
    package: Annotated[
        list[str] | None,
        typer.Option(
            "--package",
            "-p",
            help="Limit the run to one or more crate2nix targets.",
        ),
    ] = None,
    write: Annotated[
        bool,
        typer.Option(
            "--write",
            "-w",
            help="Write refreshed Cargo.nix and crate-hashes.json files back to the repo.",
        ),
    ] = False,
) -> None:
    """Check or refresh checked-in crate2nix artifacts."""
    raise typer.Exit(code=run(packages=tuple(package or ()), write=write))


main = make_main(app, prog_name="pipeline crate2nix")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "REPO_ROOT",
    "TARGETS",
    "Crate2NixTarget",
    "RefreshResult",
    "_current_platform",
    "_normalize_json_text",
    "_normalize_trailing_newline",
    "_refresh_target",
    "_resolve_targets",
    "_stabilize_generated_command_comment",
    "_stabilize_generated_root_src_paths",
    "_target_has_changes",
    "_write_target",
    "app",
    "cli",
    "crate2nix_artifact_updates",
    "main",
    "run",
    "stream_crate2nix_artifact_updates",
]
