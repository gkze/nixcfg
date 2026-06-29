#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in Goose Cargo.nix.

crate2nix assumes the generated Cargo.nix lives next to the workspace source.
In this repo we instead check Cargo.nix into overlays/goose-cli/ and feed the
real, already-patched Goose source tree separately via rootSrc.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_repo_import_path() -> None:
    """Add the repository root to ``sys.path`` for direct script execution."""
    if env_root := os.environ.get("REPO_ROOT"):
        sys.path.insert(0, str(Path(env_root).expanduser().resolve()))
        return

    candidates: list[Path] = []
    cwd = Path.cwd().resolve()
    candidates.extend((cwd, *cwd.parents))

    script_path = Path(__file__).resolve()
    for candidate in (script_path.parent, *script_path.parents):
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if (candidate / ".root").is_file():
            sys.path.insert(0, str(candidate))
            return

    msg = f"Could not find repo root for {script_path}"
    raise RuntimeError(msg)


_bootstrap_repo_import_path()

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402
from lib.cargo_nix_normalizer_cli import (  # noqa: E402
    resolve_path as resolve_cli_path,
)
from lib.cargo_nix_normalizer_cli import run_normalizer  # noqa: E402
from lib.update.paths import get_repo_root  # noqa: E402


def _resolve_path(path_text: str) -> Path:
    """Resolve one CLI path against the repository root."""
    return resolve_cli_path(path_text, repo_root=get_repo_root())


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Goose Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=("crates", "vendor"),
    )


def main(argv: list[str] | None = None) -> int:
    """Normalize a Goose Cargo.nix file in place and report what changed."""
    return run_normalizer(
        normalize=normalize,
        default_path="overlays/goose-cli/Cargo.nix",
        description=__doc__,
        argv=argv,
        repo_root=get_repo_root(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
