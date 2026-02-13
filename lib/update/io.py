"""Atomic file-write utilities."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, mkdir: bool = False) -> None:
    """Atomically write *content* to *path* via temp-file, fsync, and rename.

    If *path* already exists its permission bits are preserved.  When
    *mkdir* is ``True`` the parent directory is created if it does not
    exist.
    """
    if mkdir:
        path.parent.mkdir(parents=True, exist_ok=True)

    mode = path.stat().st_mode & 0o777 if path.exists() else None
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = Path(tmp_file.name)
            if mode is not None:
                os.fchmod(tmp_file.fileno(), mode)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
