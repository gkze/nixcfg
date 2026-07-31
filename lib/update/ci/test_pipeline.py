"""Run the current-tree gates from the hosted CI workflow."""

from __future__ import annotations

import shlex
import subprocess

import typer

from lib.update.ci._cli import make_typer_app
from lib.update.ci.workflow_defs import DEEP_QUALITY_CHECKS, FAST_QUALITY_CHECKS
from lib.update.paths import get_repo_root

_QUALITY_SYSTEM = "x86_64-linux"
_CURRENT_TREE_COMMANDS = (
    *(
        ("nix", "build", f"path:.#checks.{_QUALITY_SYSTEM}.{check}")
        for check in (*FAST_QUALITY_CHECKS, *DEEP_QUALITY_CHECKS)
    ),
    (
        "nix",
        "run",
        "--inputs-from",
        "path:.",
        "nixpkgs#pinact",
        "--",
        "run",
        "--check",
    ),
    ("nix", "run", "path:.#nixcfg", "--", "ci", "pipeline", "crate2nix"),
)


def _run_command(command: tuple[str, ...]) -> int:
    typer.echo(f"+ {shlex.join(command)}", err=True)
    return subprocess.run(  # noqa: S603
        command,
        cwd=get_repo_root(),
        check=False,
    ).returncode


def run() -> int:
    """Run the hosted workflow's current-tree gates, failing fast."""
    for command in _CURRENT_TREE_COMMANDS:
        if returncode := _run_command(command):
            return returncode
    return 0


app = make_typer_app(
    help_text=(
        "Run hosted CI's current-tree gates on x86_64-linux. "
        "Commit-range linting remains GitHub-only."
    ),
)


@app.callback(invoke_without_command=True)
def cli() -> None:
    """Run quality, action-pin, and crate2nix freshness gates."""
    raise typer.Exit(code=run())
