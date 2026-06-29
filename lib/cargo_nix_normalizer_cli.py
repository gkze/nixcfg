"""Shared CLI runner for checked-in crate2nix normalizer wrappers."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from lib.update.paths import get_repo_root

type CargoNixNormalizer = Callable[[str], tuple[str, int, bool]]


def resolve_path(path_text: str, *, repo_root: Path | None = None) -> Path:
    """Resolve one CLI path against the repository root."""
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    root = get_repo_root() if repo_root is None else repo_root
    return root / path


def run_normalizer(
    *,
    normalize: CargoNixNormalizer,
    default_path: str,
    description: str | None,
    argv: list[str] | None = None,
    repo_root: Path | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Normalize a Cargo.nix file in place and report what changed."""
    root = get_repo_root() if repo_root is None else repo_root
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(root / default_path),
    )
    args = parser.parse_args(argv)

    path = resolve_path(args.path, repo_root=root)
    original = path.read_text()
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized)

    status = [
        "added rootSrc" if added_root_src else "rootSrc already present",
        f"rewrote {path_rewrites} source path(s)",
        "updated file" if normalized != original else "no content change",
    ]
    output = sys.stdout if stdout is None else stdout
    output.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


__all__ = ["CargoNixNormalizer", "resolve_path", "run_normalizer"]
