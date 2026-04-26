"""Repository path helpers used by update modules."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT_MARKER = ".root"


class _CacheClearable(Protocol):
    cache_clear: Callable[[], None]


def _search_anchor(start: os.PathLike[str] | str | None = None) -> Path:
    """Return the directory from which repo-root discovery should begin."""
    anchor = Path.cwd() if start is None else Path(start).expanduser()
    resolved = anchor.resolve()
    if resolved.exists() and resolved.is_file():
        return resolved.parent
    return resolved


@cache
def _find_root_cached(env_root: str | None, start: str) -> Path:
    """Resolve the repository root from an environment override or search anchor."""
    if env_root:
        return Path(env_root).expanduser().resolve()

    anchor = Path(start).expanduser().resolve()
    for candidate in (anchor, *anchor.parents):
        if (candidate / ROOT_MARKER).is_file():
            return candidate

    msg = (
        f"Could not find repo root from {anchor}; expected {ROOT_MARKER} in an ancestor"
    )
    raise RuntimeError(msg)


def _clear_root_cache() -> None:
    """Clear the shared cached repo-root lookup."""
    _find_root_cached.cache_clear()


def find_root(start: os.PathLike[str] | str | None = None) -> Path:
    """Return the repository root discovered from ``start`` or ``cwd``.

    Resolution prefers ``$REPO_ROOT`` when set. Otherwise the search walks upward from
    ``start`` (or the current working directory) until it finds ``.root``.
    """
    anchor = _search_anchor(start)
    return _find_root_cached(os.environ.get("REPO_ROOT"), os.fspath(anchor))


cast("_CacheClearable", find_root).cache_clear = _clear_root_cache


def find_repo_root(start: os.PathLike[str] | str | None = None) -> Path:
    """Compatibility wrapper around :func:`find_root`."""
    return find_root(start)


cast("_CacheClearable", find_repo_root).cache_clear = _clear_root_cache


def get_repo_root() -> Path:
    """Return the current repository root."""
    return find_root()


cast("_CacheClearable", get_repo_root).cache_clear = _clear_root_cache


class _RepoPathProxy(os.PathLike[str]):
    """Lazy path-like wrapper rooted on :func:`find_root`."""

    def __init__(self, relative_path: str | None = None) -> None:
        self._relative_path = relative_path

    def _path(self) -> Path:
        root = get_repo_root()
        if self._relative_path is None:
            return root
        return root / self._relative_path

    def __fspath__(self) -> str:
        return os.fspath(self._path())

    def __truediv__(self, other: str) -> Path:
        return self._path() / other

    def __getattr__(self, name: str) -> object:
        return getattr(self._path(), name)

    def __repr__(self) -> str:
        return repr(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Path):
            return self._path() == other
        if isinstance(other, str):
            return self._path() == Path(other)
        if isinstance(other, os.PathLike):
            other_path = os.fspath(other)
            return (
                self._path() == Path(other_path)
                if isinstance(other_path, str)
                else False
            )
        return False

    def __hash__(self) -> int:
        return hash(self._path())


REPO_ROOT: Path = cast("Path", _RepoPathProxy())


# Top-level directories containing package and overlay metadata files.
PACKAGE_DIRS = ("packages", "overlays")

SOURCES_FILE_NAME = "sources.json"


def package_file_git_pathspecs(filename: str) -> tuple[str, ...]:
    """Return git pathspecs for directory and flat per-package files."""
    return tuple(
        pathspec
        for directory in PACKAGE_DIRS
        for pathspec in (
            f":(glob){directory}/**/{filename}",
            f":(glob){directory}/*.{filename}",
        )
    )


SOURCES_GIT_PATHSPECS = package_file_git_pathspecs(SOURCES_FILE_NAME)


def is_package_file_path(relative_path: str, filename: str) -> bool:
    """Return whether a relative path matches per-package file layouts."""
    if not any(relative_path.startswith(f"{directory}/") for directory in PACKAGE_DIRS):
        return False
    return relative_path.endswith((f"/{filename}", f".{filename}"))


def is_sources_file_path(relative_path: str) -> bool:
    """Return whether a relative path points at a per-package sources file."""
    return is_package_file_path(relative_path, SOURCES_FILE_NAME)


def get_repo_file(filename: str) -> Path:
    """Return a concrete path under the detected repository root."""
    return get_repo_root() / filename


@cache
def _package_file_map_cached(
    root: Path,
    filename: str,
) -> tuple[tuple[str, Path], ...]:
    """Return cached ``(name, path)`` pairs for per-package files."""
    return tuple(sorted(_package_file_map(root, filename).items()))


def _flat_package_file_name(name: str, filename: str) -> str | None:
    """Return package name for ``<name>.<filename>`` files, if matched."""
    suffix = f".{filename}"
    if not name.endswith(suffix):
        return None
    package_name = name[: -len(suffix)]
    if not package_name:
        return None
    return package_name


def _package_file_map(root: Path, filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for per-package sidecar files.

    Supports two layouts under ``packages/`` and ``overlays/``:
    1. Directory-based: ``<name>/<filename>``
    2. Flat file: ``<name>.<filename>``
    """
    result: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}

    def _record(name: str, path: Path) -> None:
        if name in result:
            duplicates.setdefault(name, [result[name]]).append(path)
            return
        result[name] = path

    for d in PACKAGE_DIRS:
        pkg_root = root / d
        if not pkg_root.is_dir():
            continue
        for child in sorted(pkg_root.iterdir()):
            if child.is_dir():
                candidate = child / filename
                if not candidate.exists():
                    continue
                _record(child.name, candidate)
                continue

            if not child.is_file():
                continue

            if (flat_name := _flat_package_file_name(child.name, filename)) is None:
                continue

            _record(flat_name, child)

    if duplicates:
        lines = [f"Duplicate per-package {filename} entries detected:"]
        for name in sorted(duplicates):
            paths = ", ".join(str(p.relative_to(root)) for p in duplicates[name])
            lines.append(f"- {name}: {paths}")
        raise RuntimeError("\n".join(lines))

    return result


def package_file_map_in(root: Path, filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for package files under an arbitrary root."""
    return _package_file_map(root.resolve(), filename)


def package_file_map(filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for per-package files named ``filename``."""
    return dict(_package_file_map_cached(get_repo_root(), filename))


def package_file_for(name: str, filename: str) -> Path | None:
    """Return the package file path for ``name`` and ``filename`` or ``None``."""
    return package_file_map(filename).get(name)


def package_dirs_for_in(root: Path, name: str) -> list[Path]:
    """Return matching package directories for ``name`` under ``root``."""
    resolved_root = root.resolve()
    return [
        candidate
        for d in PACKAGE_DIRS
        if (candidate := resolved_root / d / name).is_dir()
    ]


def _unique_package_dir_for(root: Path, name: str) -> Path | None:
    """Return a unique matching package directory or raise on duplicates."""
    resolved_root = root.resolve()
    matches = package_dirs_for_in(resolved_root, name)

    if not matches:
        return None
    if len(matches) > 1:
        paths = ", ".join(str(path.relative_to(resolved_root)) for path in matches)
        msg = f"Duplicate package directories for '{name}': {paths}"
        raise RuntimeError(msg)
    return matches[0]


def package_dir_for_in(root: Path, name: str) -> Path | None:
    """Return the unique package directory for ``name`` under ``root``."""
    return _unique_package_dir_for(root, name)


def package_dir_for(name: str) -> Path | None:
    """Return the unique package directory for ``name`` or ``None``."""
    return _unique_package_dir_for(get_repo_root(), name)


def sources_file_for(name: str) -> Path | None:
    """Return the ``sources.json`` path for a named package, or ``None``."""
    return package_file_for(name, SOURCES_FILE_NAME)
