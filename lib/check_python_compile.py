"""Compile configured Python files without polluting repo-local __pycache__ trees."""

from __future__ import annotations

import argparse
import compileall
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_IGNORED_SCAN_DIRS: Final = frozenset({
    ".claude",
    ".direnv",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "result",
})
_GLOB_TOKENS: Final = frozenset("*?[")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile Python sources and stubs without writing __pycache__ into "
            "the repo tree."
        ),
    )
    parser.add_argument("paths", nargs="+", help="Python file paths or glob patterns.")
    return parser


def _has_glob(pattern: str) -> bool:
    return any(token in pattern for token in _GLOB_TOKENS)


def _is_ignored(path: Path) -> bool:
    return any(part in _IGNORED_SCAN_DIRS for part in path.parts)


def _matches_glob(path: Path, pattern: str) -> bool:
    candidate = PurePosixPath(path.as_posix())
    return candidate.match(pattern) or (
        pattern.startswith("**/") and candidate.match(pattern.removeprefix("**/"))
    )


def _iter_non_ignored_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        relative_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [
            dirname for dirname in dirnames if not _is_ignored(relative_dir / dirname)
        ]
        for filename in filenames:
            relative = relative_dir / filename
            if not _is_ignored(relative):
                yield relative


def iter_target_paths(
    patterns: Iterable[str],
    *,
    root: Path | None = None,
) -> Iterator[Path]:
    """Yield deduplicated compile targets for *patterns* under *root*."""
    resolved_root = Path() if root is None else root
    seen: set[str] = set()

    for pattern in patterns:
        if _has_glob(pattern):
            candidates = _iter_non_ignored_files(resolved_root)
        else:
            candidates = [Path(pattern)]

        for relative in candidates:
            if _has_glob(pattern):
                if not _matches_glob(relative, pattern):
                    continue
            elif relative != Path(pattern):
                continue

            candidate = resolved_root / relative
            if not candidate.is_file():
                continue
            key = relative.as_posix()
            if key in seen:
                continue
            seen.add(key)
            yield relative


def compile_paths(patterns: Iterable[str]) -> bool:
    """Return whether all expanded targets compile successfully."""
    targets = list(iter_target_paths(patterns))
    previous_prefix = sys.pycache_prefix

    with tempfile.TemporaryDirectory(prefix="nixcfg-pycache-") as pycache_prefix:
        if previous_prefix is None:
            sys.pycache_prefix = pycache_prefix
        try:
            failed = False
            for path in targets:
                if not compileall.compile_file(
                    str(path),
                    quiet=1,
                    force=True,
                ):
                    failed = True
            return not failed
        finally:
            sys.pycache_prefix = previous_prefix


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the compile smoke check."""
    args = _build_parser().parse_args(argv)
    return 0 if compile_paths(args.paths) else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
