"""Render shell completion scripts for the packaged nixcfg CLI."""

from __future__ import annotations

import sys

from typer._completion_shared import get_completion_script

_EXPECTED_ARGC = 1
_PROG_NAME = "nixcfg"
_COMPLETE_VAR = "_NIXCFG_COMPLETE"


def render_completion(shell: str) -> str:
    """Return the Typer completion script for one shell."""
    return get_completion_script(
        prog_name=_PROG_NAME,
        complete_var=_COMPLETE_VAR,
        shell=shell,
    )


def main(argv: list[str] | None = None) -> int:
    """Print one requested completion script."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != _EXPECTED_ARGC:
        msg = "usage: render_completion.py <shell>"
        raise SystemExit(msg)
    sys.stdout.write(render_completion(args[0]) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
