"""Repository path helpers used by update modules."""

import os
from functools import cache
from pathlib import Path
from typing import cast


def _resolve_repo_root() -> Path:
    env_root = os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    script_path = Path(__file__).resolve()
    if "/nix/store" not in str(script_path):
        return script_path.parents[2]

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "flake.nix").exists():
            return candidate
    return cwd


@cache
def get_repo_root() -> Path:
    """Return the current repository root, honoring runtime env overrides."""
    return _resolve_repo_root()


class _RepoRootProxy(os.PathLike[str]):
    def _path(self) -> Path:
        get_repo_root.cache_clear()
        return get_repo_root()

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


REPO_ROOT: Path = cast("Path", _RepoRootProxy())

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
    """Return a path under the detected repository root."""
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


FLAKE_LOCK_FILE = get_repo_file("flake.lock")
