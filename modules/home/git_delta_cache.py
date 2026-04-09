"""Cache-aware delta wrapper that rebuilds bat metadata on demand."""

# ruff: noqa: INP001

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

BAT = "@BAT@"
DELTA = "@DELTA@"


def _xdg_home(env_name: str, fallback: str) -> Path:
    """Resolve an XDG base directory with a HOME-based fallback."""
    value = os.environ.get(env_name)
    return Path(value) if value else Path.home() / fallback


def _tree_has_newer_regular_files(directory: Path, reference: Path) -> bool:
    """Return whether any regular file under ``directory`` is newer than ``reference``."""
    try:
        reference_mtime_ns = reference.stat().st_mtime_ns
    except OSError:
        return True

    try:
        for path in directory.rglob("*"):
            try:
                stat_result = path.lstat()
            except OSError:
                continue
            if (
                stat.S_ISREG(stat_result.st_mode)
                and stat_result.st_mtime_ns > reference_mtime_ns
            ):
                return True
    except OSError:
        return False

    return False


def _needs_rebuild(cache_dir: Path, config_dir: Path) -> bool:
    """Return whether the bat cache should be rebuilt before running delta."""
    themes_bin = cache_dir / "themes.bin"
    syntaxes_bin = cache_dir / "syntaxes.bin"

    if not themes_bin.is_file() or not syntaxes_bin.is_file():
        return True

    if (config_dir / "themes").is_dir() and _tree_has_newer_regular_files(
        config_dir / "themes", themes_bin
    ):
        return True

    return (config_dir / "syntaxes").is_dir() and _tree_has_newer_regular_files(
        config_dir / "syntaxes", syntaxes_bin
    )


def _rebuild_cache(cache_home: Path) -> None:
    """Best-effort bat cache rebuild, suppressing tool output and failures."""
    (cache_home / "bat").mkdir(parents=True, exist_ok=True)
    tmp_root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="delta-bat-cache.", dir=tmp_root) as tmpdir:
        env = os.environ.copy()
        env["XDG_CACHE_HOME"] = os.fspath(cache_home)
        subprocess.run(  # noqa: S603
            [BAT, "cache", "--build"],
            check=False,
            cwd=tmpdir,
            env=env,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )


def main(argv: list[str]) -> None:
    """Refresh bat cache metadata if needed, then replace the process with delta."""
    cache_home = _xdg_home("XDG_CACHE_HOME", ".cache")
    config_home = _xdg_home("XDG_CONFIG_HOME", ".config")

    if _needs_rebuild(cache_home / "bat", config_home / "bat"):
        _rebuild_cache(cache_home)

    os.execv(DELTA, [DELTA, *argv])  # noqa: S606


if __name__ == "__main__":
    main(sys.argv[1:])
