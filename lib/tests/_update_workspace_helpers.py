"""Shared repository setup for isolated update-workspace tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping


def init_update_workspace_repo(
    root: Path,
    *,
    tracked_files: Mapping[str, str] | None = None,
    gitignore: str | None = "ignored.txt\n",
) -> None:
    """Create the minimal committed repository used by workspace tests."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is required")
    root.mkdir()
    (root / ".root").write_text("\n", encoding="utf-8")
    if gitignore is not None:
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    files = tracked_files or {
        "tracked.txt": "committed\n",
        "nested/tracked.txt": "nested\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run([git, "init", "--quiet"], cwd=root, check=True)  # noqa: S603
    subprocess.run([git, "add", "--all"], cwd=root, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            git,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        cwd=root,
        check=True,
    )


__all__ = ["init_update_workspace_repo"]
