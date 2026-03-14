"""Shared helpers for recovery subcommands."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def files_equal(left: Path, right: Path) -> bool:
    """Return whether two files exist and have identical contents."""
    if not left.exists() or not right.exists():
        return False
    return left.read_bytes() == right.read_bytes()


def stage_paths(repo_root: Path, paths: tuple[str, ...]) -> None:
    """Stage *paths* in Git, including deletions."""
    if not paths:
        return
    git_bin = shutil.which("git")
    if git_bin is None:
        msg = "git not found on PATH"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [git_bin, "add", "-A", "--", *paths],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "git add failed"
        raise RuntimeError(stderr)
