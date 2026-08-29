"""Shared file runner for package-specific crate2nix normalizers."""

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

type CargoNixNormalizer = Callable[[str], tuple[str, int, bool]]


def normalize_file(
    *,
    normalize: CargoNixNormalizer,
    path: Path,
) -> int:
    """Normalize a Cargo.nix file in place and report what changed."""
    original = path.read_text(encoding="utf-8")
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized, encoding="utf-8")

    status = [
        "added rootSrc" if added_root_src else "rootSrc already present",
        f"rewrote {path_rewrites} source path(s)",
        "updated file" if normalized != original else "no content change",
    ]
    sys.stdout.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


__all__ = ["CargoNixNormalizer", "normalize_file"]
