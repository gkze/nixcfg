"""Normalize generated crate2nix output for the checked-in Zed Cargo.nix."""

import re

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix

_STORE_SOURCE_PATTERN = re.compile(
    r'(?P<needle>"?(?:\.\./)+nix/store/[^/]+/(?P<suffix>[^";]+)"?)'
)


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Zed Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        fallback_patterns=(_STORE_SOURCE_PATTERN,),
        rewrite_nixpkgs_config=True,
    )
