#!/usr/bin/env python3
"""Normalize generated crate2nix output for opencode-desktop."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


sys.path.insert(0, str(_repo_root()))

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402

_STORE_SOURCE_PATTERN = re.compile(
    r'(?P<needle>"?(?:\.\./)+nix/store/[^/]+/(?P<suffix>[^";]+)"?)'
)


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized Cargo.nix text plus replacement counts."""
    return normalize_cargo_nix(
        text,
        local_path_prefixes=("packages/desktop/src-tauri",),
        fallback_patterns=(_STORE_SOURCE_PATTERN,),
        rewrite_nixpkgs_config=True,
    )


def main() -> int:
    """Normalize a Cargo.nix file in place and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path", nargs="?", default="packages/opencode-desktop/Cargo.nix"
    )
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
