"""Repository path helpers used by update modules."""

import os
from functools import cache
from pathlib import Path


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


REPO_ROOT = _resolve_repo_root()

# Directories containing per-package sources.json and updater.py files.
PACKAGE_DIRS = ("packages", "overlays")


def get_repo_file(filename: str) -> Path:
    """Return a path under the detected repository root."""
    return REPO_ROOT / filename


@cache
def _package_file_map_cached(filename: str) -> tuple[tuple[str, Path], ...]:
    """Return cached ``(name, path)`` pairs for per-package files."""
    return tuple(sorted(_package_file_map(REPO_ROOT, filename).items()))


def _package_file_map(root: Path, filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for package subdirs containing ``filename``."""
    result: dict[str, Path] = {}
    duplicates: dict[str, list[Path]] = {}
    for d in PACKAGE_DIRS:
        pkg_root = root / d
        if not pkg_root.is_dir():
            continue
        for child in sorted(pkg_root.iterdir()):
            candidate = child / filename
            if not child.is_dir() or not candidate.exists():
                continue
            if child.name in result:
                duplicates.setdefault(child.name, [result[child.name]]).append(
                    candidate,
                )
                continue
            result[child.name] = candidate

    if duplicates:
        lines = [f"Duplicate per-package {filename} entries detected:"]
        for name in sorted(duplicates):
            paths = ", ".join(str(p.relative_to(root)) for p in duplicates[name])
            lines.append(f"- {name}: {paths}")
        raise RuntimeError("\n".join(lines))

    return result


def package_file_map_in(root: Path, filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for package subdirs under an arbitrary root."""
    return _package_file_map(root.resolve(), filename)


def package_file_map(filename: str) -> dict[str, Path]:
    """Return ``{name: path}`` for package subdirs containing ``filename``."""
    return dict(_package_file_map_cached(filename))


def package_file_for(name: str, filename: str) -> Path | None:
    """Return the package file path for ``name`` and ``filename`` or ``None``."""
    return package_file_map(filename).get(name)


def package_dir_for(name: str) -> Path | None:
    """Return the unique package directory for ``name`` or ``None``."""
    matches: list[Path] = []
    for d in PACKAGE_DIRS:
        candidate = REPO_ROOT / d / name
        if candidate.is_dir():
            matches.append(candidate)

    if not matches:
        return None
    if len(matches) > 1:
        paths = ", ".join(str(path.relative_to(REPO_ROOT)) for path in matches)
        msg = f"Duplicate package directories for '{name}': {paths}"
        raise RuntimeError(msg)
    return matches[0]


def sources_file_for(name: str) -> Path | None:
    """Return the ``sources.json`` path for a named package, or ``None``."""
    return package_file_for(name, "sources.json")


FLAKE_LOCK_FILE = get_repo_file("flake.lock")
