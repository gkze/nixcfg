"""Generate a canonical unified diff for source-entry JSON changes."""

from __future__ import annotations

import difflib
import json
import pathlib
import sys
from typing import Annotated

import typer

from lib import json_utils
from lib.update.ci._cli import (
    make_dual_typer_apps,
    make_main,
    register_dual_entrypoint,
)

JsonValue = json_utils.JsonValue
NoChangesMessage = "No source entry changes detected."


def _read_json(path: pathlib.Path) -> dict[str, JsonValue]:
    with path.open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    return json_utils.coerce_json_object(loaded, context=str(path))


def _canonical_lines(payload: dict[str, JsonValue]) -> list[str]:
    return json.dumps(payload, indent=2, sort_keys=True).splitlines()


def run_diff(old_path: pathlib.Path, new_path: pathlib.Path) -> str:
    """Compare two source-entry JSON files using one deterministic format."""
    old_data = _read_json(pathlib.Path(old_path))
    new_data = _read_json(pathlib.Path(new_path))
    if old_data == new_data:
        return NoChangesMessage

    return "\n".join(
        difflib.unified_diff(
            _canonical_lines(old_data),
            _canonical_lines(new_data),
            fromfile="old/source-entry.json",
            tofile="new/source-entry.json",
            lineterm="",
        )
    )


def run(*, old_sources: pathlib.Path, new_sources: pathlib.Path) -> int:
    """Render a canonical diff between two source-entry JSON files."""
    sys.stdout.write(run_diff(old_sources, new_sources))
    sys.stdout.write("\n")
    return 0


_DUAL_APPS = make_dual_typer_apps(
    help_text="Generate a canonical unified diff for source entry JSON changes.",
    no_args_is_help=False,
)
app = _DUAL_APPS.app


@register_dual_entrypoint(_DUAL_APPS)
def cli(
    old_sources: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to old source JSON file."),
    ],
    new_sources: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to new source JSON file."),
    ],
) -> None:
    """Compare source-entry JSON files and print a canonical unified diff."""
    raise typer.Exit(
        code=run(
            old_sources=old_sources,
            new_sources=new_sources,
        )
    )


main = make_main(_DUAL_APPS.standalone_app, prog_name="diff sources")


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    raise SystemExit(main())
