"""Shared helpers for recovery subcommands."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from lib.update import io as update_io

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


def apply_recovery_paths(
    *,
    repo_root: Path,
    snapshot_root: Path,
    write_paths: tuple[str, ...],
    remove_paths: tuple[str, ...],
    stage: bool,
) -> tuple[str, ...]:
    """Restore and remove planned paths, optionally staging changed files."""
    changed_paths: list[str] = []

    for relative_path in write_paths:
        source_path = snapshot_root / relative_path
        target_path = repo_root / relative_path
        update_io.atomic_write_bytes(target_path, source_path.read_bytes(), mkdir=True)
        changed_paths.append(relative_path)

    for relative_path in remove_paths:
        target_path = repo_root / relative_path
        if target_path.exists():
            target_path.unlink()
            changed_paths.append(relative_path)

    changed_tuple = tuple(changed_paths)
    if stage:
        stage_paths(repo_root, changed_tuple)
    return changed_tuple
