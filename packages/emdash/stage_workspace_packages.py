"""Stage pnpm-selected workspace packages under their manifest-owned names."""

import argparse
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_MANIFEST_BYTES = 1024 * 1024
_SCOPED_PACKAGE_COMPONENT_COUNT = 2
_PACKAGE_COMPONENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._~-]*$")


@dataclass(frozen=True, slots=True)
class WorkspacePackage:
    """One workspace path paired with the package identity it declares."""

    source: Path
    name: str

    def destination(self, node_modules: Path) -> Path:
        """Map the npm package identity into a node_modules destination."""
        return node_modules.joinpath(*self.name.split("/"))


def _package_name(payload: object, *, manifest: Path) -> str:
    if not isinstance(payload, dict):
        msg = f"Emdash workspace manifest is not an object: {manifest}"
        raise TypeError(msg)
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        msg = f"Emdash workspace manifest has no package name: {manifest}"
        raise TypeError(msg)

    components = name.split("/")
    if name.startswith("@"):
        valid = (
            len(components) == _SCOPED_PACKAGE_COMPONENT_COUNT
            and components[0].startswith("@")
            and _PACKAGE_COMPONENT_PATTERN.fullmatch(components[0][1:]) is not None
            and _PACKAGE_COMPONENT_PATTERN.fullmatch(components[1]) is not None
        )
    else:
        valid = (
            len(components) == 1
            and _PACKAGE_COMPONENT_PATTERN.fullmatch(components[0]) is not None
        )
    if not valid:
        msg = f"Emdash workspace manifest has an invalid package name {name!r}: {manifest}"
        raise RuntimeError(msg)
    return name


def _read_manifest(manifest: Path) -> object:
    try:
        if manifest.stat().st_size > _MAX_MANIFEST_BYTES:
            msg = f"Emdash workspace manifest exceeds {_MAX_MANIFEST_BYTES} bytes: {manifest}"
            raise RuntimeError(msg)
        return json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        msg = f"Emdash workspace package has no manifest: {manifest}"
        raise RuntimeError(msg) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"Emdash workspace manifest is not valid UTF-8 JSON: {manifest}"
        raise RuntimeError(msg) from exc


def workspace_packages(
    source_root: Path, path_list: Path
) -> tuple[WorkspacePackage, ...]:
    """Resolve pnpm's paths without reconstructing the workspace layout."""
    source_root = source_root.resolve(strict=True)
    try:
        raw_paths = path_list.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        msg = f"Emdash workspace path list is not UTF-8: {path_list}"
        raise RuntimeError(msg) from exc
    if not raw_paths:
        msg = "Emdash desktop has no workspace package dependencies"
        raise RuntimeError(msg)

    packages: list[WorkspacePackage] = []
    names: set[str] = set()
    sources: set[Path] = set()
    for raw_path in raw_paths:
        if not raw_path:
            msg = "Emdash workspace path list contains an empty path"
            raise RuntimeError(msg)
        source = Path(raw_path)
        if not source.is_absolute():
            source = source_root / source
        try:
            source = source.resolve(strict=True)
        except FileNotFoundError as exc:
            msg = f"Emdash workspace package path does not exist: {raw_path}"
            raise RuntimeError(msg) from exc
        if not source.is_dir() or not source.is_relative_to(source_root):
            msg = f"Emdash workspace package path escapes the source tree: {raw_path}"
            raise RuntimeError(msg)

        manifest = source / "package.json"
        name = _package_name(_read_manifest(manifest), manifest=manifest)
        if source in sources:
            msg = f"Emdash workspace path is listed more than once: {source}"
            raise RuntimeError(msg)
        if name in names:
            msg = f"Emdash workspace package name is not unique: {name}"
            raise RuntimeError(msg)
        sources.add(source)
        names.add(name)
        packages.append(WorkspacePackage(source=source, name=name))
    return tuple(packages)


def _remove_destination(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)


def stage_workspace_packages(
    packages: tuple[WorkspacePackage, ...],
    node_modules: Path,
    *,
    mode: str,
) -> None:
    """Link packages for the workspace build, then copy their built trees."""
    if mode not in {"copy", "link"}:
        msg = f"Unsupported Emdash workspace staging mode: {mode}"
        raise ValueError(msg)
    node_modules.mkdir(parents=True, exist_ok=True)
    for package in packages:
        destination = package.destination(node_modules)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _remove_destination(destination)
        if mode == "link":
            destination.symlink_to(package.source, target_is_directory=True)
        else:
            shutil.copytree(package.source, destination, symlinks=True)


def _retry_readonly(
    function: Callable[[str], object],
    path: str,
    exc: BaseException,
) -> None:
    """Retry a delete after clearing a read-only bit left by pnpm."""
    if not isinstance(exc, PermissionError):
        raise exc
    target = Path(path)
    target.chmod(stat.S_IRWXU)
    target.parent.chmod(stat.S_IRWXU)
    function(path)


def clean_build_node_modules(root: Path) -> None:
    """Remove build-time node_modules without following directory symlinks.

    Hosted Darwin Nix walks directory symlinks when deleting the sandbox.
    The desktop ``node_modules -> ../../node_modules`` link plus staged
    workspace copies make that walk cyclic, and cleanup dies with
    ``cannot unlink .../source/node_modules: Directory not empty``.
    """
    root = root.resolve(strict=True)
    targets: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        retained: list[str] = []
        for name in dirnames:
            path = Path(dirpath, name)
            if name == "node_modules":
                targets.append(path)
                continue
            if path.is_symlink():
                continue
            retained.append(name)
        dirnames[:] = retained
        targets.extend(
            Path(dirpath, name) for name in filenames if name == "node_modules"
        )

    for path in sorted(targets, key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path, onexc=_retry_readonly)


def main(argv: list[str] | None = None) -> None:
    """Stage the exact package paths selected by pnpm, or strip build node_modules."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("link", "copy", "clean"))
    parser.add_argument("source_root", type=Path)
    parser.add_argument("node_modules", type=Path, nargs="?")
    parser.add_argument("path_list", type=Path, nargs="?")
    args = parser.parse_args(argv)
    if args.mode == "clean":
        if args.node_modules is not None or args.path_list is not None:
            parser.error("clean takes only source_root")
        clean_build_node_modules(args.source_root)
        return
    if args.node_modules is None or args.path_list is None:
        parser.error("link and copy require node_modules and path_list")
    packages = workspace_packages(args.source_root, args.path_list)
    stage_workspace_packages(packages, args.node_modules, mode=args.mode)


if __name__ == "__main__":  # pragma: no cover -- standard command-line entry point
    main()
