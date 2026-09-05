"""Stage pnpm-selected workspace packages under their manifest-owned names."""

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

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


def main(argv: list[str] | None = None) -> None:
    """Stage the exact package paths selected by pnpm."""
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("link", "copy"))
    parser.add_argument("source_root", type=Path)
    parser.add_argument("node_modules", type=Path)
    parser.add_argument("path_list", type=Path)
    args = parser.parse_args(argv)
    packages = workspace_packages(args.source_root, args.path_list)
    stage_workspace_packages(packages, args.node_modules, mode=args.mode)


if __name__ == "__main__":  # pragma: no cover -- standard command-line entry point
    main()
