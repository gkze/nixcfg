"""Map Actions artifact names onto Buildkite artifact paths.

Uploads land under ``bk-artifacts/<name>/``. A named download extracts that
directory into the destination. A download with no name recreates
``dest/<name>/`` for every artifact, which is where coverage reads
``evidence/prepare-x86_64-linux/candidate.json``.
"""

import shutil
from typing import TYPE_CHECKING

from lib.buildkite.graph import WorkflowParseError

if TYPE_CHECKING:
    from pathlib import Path


def stage_upload(staging: Path, name: str, source: Path) -> int:
    """Copy *source* into ``staging/bk-artifacts/<name>`` and return the file count."""
    target = staging / "bk-artifacts" / name
    if source.is_file():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / source.name)
        return 1
    if not source.is_dir():
        return 0
    count = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        count += 1
    return count


def arrange_download(staging: Path, dest: Path, name: str | None) -> None:
    """Move a Buildkite download into the Actions destination layout.

    *staging* contains ``bk-artifacts/<name>/``. A missing named artifact is an
    error. A missing download-all directory leaves *dest* empty.
    """
    source = staging / "bk-artifacts"
    dest.mkdir(parents=True, exist_ok=True)
    if name is None:
        if not source.is_dir():
            return
        for child in source.iterdir():
            _copy_tree(child, dest / child.name)
        return
    nested = source / name
    if not nested.exists():
        message = f"missing artifact {name}"
        raise WorkflowParseError(message)
    _copy_tree(nested, dest)


def _copy_tree(source: Path, dest: Path) -> None:
    if source.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return
    shutil.copytree(source, dest, dirs_exist_ok=True)
