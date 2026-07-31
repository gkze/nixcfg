"""Normalize generated crate2nix output for the checked-in Goose Cargo.nix.

crate2nix assumes the generated Cargo.nix lives next to the workspace source.
In this repo we instead check Cargo.nix into overlays/goose-cli/ and feed the
real, already-patched Goose source tree separately via rootSrc.
"""

from __future__ import annotations

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Goose Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=("crates", "vendor"),
    )
