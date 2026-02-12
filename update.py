#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "aiohttp>=3.13.3",
#   "aiohttp-retry>=2.9.1",
#   "filelock>=3.20.3",
#   "keyring>=25.7.0",
#   "lz4>=4.4.5",
#   "nix-manipulator @ git+https://github.com/hoh/nix-manipulator.git@dbe47853d2f48b6314a9e07e5bad6ba78bdbf6bc",
#   "packaging>=26.0",
#   "pydantic>=2.12.5",
#   "pydantic-settings>=2.12.0",
#   "pyyaml>=6.0.3",
#   "rich>=14.3.2",
#   "typer>=0.23.0",
# ]
# ///

"""Entry-point script for update workflows."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path


def _reexec_with_uv_on_missing_dependency() -> None:
    """Re-exec with `uv run --script` when direct Python misses script deps."""
    if os.environ.get("UPDATE_PY_UV_REEXEC") == "1":
        return
    uv = shutil.which("uv")
    if uv is None:
        return
    script_path = str(Path(__file__).resolve())
    cmd = [uv, "run", "--script", script_path, *sys.argv[1:]]
    env = os.environ.copy()
    env["UPDATE_PY_UV_REEXEC"] = "1"
    os.execve(uv, cmd, env)  # noqa: S606


def main() -> int:
    """Run update CLI (including CI helper dispatch)."""
    try:
        ci_commands = importlib.import_module("lib.update.ci").CI_COMMANDS
        ci_main = importlib.import_module("lib.update.ci").main
        update_main = importlib.import_module("lib.update.cli").main
    except ModuleNotFoundError:
        _reexec_with_uv_on_missing_dependency()
        raise

    if len(sys.argv) > 1 and sys.argv[1] in ci_commands:
        return ci_main(sys.argv[1:])

    update_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
