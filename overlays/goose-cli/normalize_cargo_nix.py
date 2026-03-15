#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in Goose Cargo.nix.

crate2nix assumes the generated Cargo.nix lives next to the workspace source.
In this repo we instead check Cargo.nix into overlays/goose-cli/ and feed the
real, already-patched Goose source tree separately via rootSrc.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root()))

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Goose Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=("crates", "vendor"),
    )


def main() -> int:
    """Normalize a Goose Cargo.nix file in place and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="overlays/goose-cli/Cargo.nix")
    args = parser.parse_args()

    path = Path(args.path)
    original = path.read_text()
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized)

    status = []
    status.append("added rootSrc" if added_root_src else "rootSrc already present")
    status.append(f"rewrote {path_rewrites} source path(s)")
    status.append("updated file" if normalized != original else "no content change")
    sys.stdout.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
