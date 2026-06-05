"""Render the minimal runtime ``package.json`` for the packaged T3 Code desktop app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import yaml

JsonObject = dict[str, Any]
JsonStringMap = dict[str, str]


def _load_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object in {path}"
        raise TypeError(msg)
    return cast("JsonObject", payload)


def _load_yaml(path: Path) -> JsonObject:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected a YAML object in {path}"
        raise TypeError(msg)
    return cast("JsonObject", payload)


def _require_object(parent: JsonObject, key: str, *, context: str) -> JsonObject:
    value = parent.get(key)
    if not isinstance(value, dict):
        msg = f"Expected '{key}' to be a JSON object in {context}"
        raise TypeError(msg)
    return cast("JsonObject", value)


def _require_string(parent: JsonObject, key: str, *, context: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Expected non-empty string '{key}' in {context}"
        raise TypeError(msg)
    return value


def _string_map(parent: JsonObject, key: str, *, context: str) -> JsonStringMap:
    value = parent.get(key, {})
    if not isinstance(value, dict):
        msg = f"Expected '{key}' to be a JSON object in {context}"
        raise TypeError(msg)
    rendered: JsonStringMap = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not isinstance(spec, str):
            msg = f"Expected string entries in '{key}' for {context}"
            raise TypeError(msg)
        rendered[name] = spec
    return rendered


def _string_list(parent: JsonObject, key: str, *, context: str) -> list[str]:
    value = parent.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"Expected '{key}' to be a string list in {context}"
        raise TypeError(msg)
    return cast("list[str]", value)


def _workspace_package_dirs(source_root: Path, patterns: list[str]) -> list[str]:
    dirs: list[str] = []
    for pattern in patterns:
        matches = (
            sorted(source_root.glob(pattern))
            if "*" in pattern
            else [source_root / pattern]
        )
        dirs.extend(
            path.relative_to(source_root).as_posix()
            for path in matches
            if (path / "package.json").is_file()
        )
    return dirs


def _workspace_package_map(
    source_root: Path,
    workspaces: JsonObject,
    *,
    context: str,
) -> dict[str, str]:
    patterns = _string_list(
        workspaces,
        "packages",
        context=context,
    )
    packages: dict[str, str] = {}
    for rel_dir in _workspace_package_dirs(source_root, patterns):
        package_json = _load_json(source_root / rel_dir / "package.json")
        package_name = _require_string(
            package_json,
            "name",
            context=f"{rel_dir}/package.json",
        )
        packages[package_name] = rel_dir
    return packages


def _workspace_metadata(
    source_root: Path,
    root_package: JsonObject,
) -> tuple[JsonObject, JsonStringMap]:
    pnpm_workspace = source_root / "pnpm-workspace.yaml"
    if pnpm_workspace.is_file():
        payload = _load_yaml(pnpm_workspace)
        workspaces: JsonObject = {
            "packages": _string_list(
                payload,
                "packages",
                context="pnpm-workspace.yaml",
            ),
            "catalog": _string_map(
                payload,
                "catalog",
                context="pnpm-workspace.yaml",
            ),
        }
        overrides = _string_map(
            payload,
            "overrides",
            context="pnpm-workspace.yaml",
        )
        return workspaces, overrides

    workspaces = _require_object(
        root_package, "workspaces", context="root package.json"
    )
    overrides = _string_map(root_package, "overrides", context="root package.json")
    return workspaces, overrides


def _workspace_dependency_names(dependencies: JsonStringMap) -> set[str]:
    return {
        name for name, spec in dependencies.items() if spec.startswith("workspace:")
    }


def _runtime_workspace_dirs(
    source_root: Path,
    workspace_packages: dict[str, str],
    dependencies: JsonStringMap,
) -> list[str]:
    selected: set[str] = set()
    pending = list(_workspace_dependency_names(dependencies))
    while pending:
        package_name = pending.pop()
        if package_name in selected:
            continue
        try:
            rel_dir = workspace_packages[package_name]
        except KeyError as exc:
            msg = f"Unable to resolve workspace dependency {package_name!r}"
            raise RuntimeError(msg) from exc
        selected.add(package_name)
        package_json = _load_json(source_root / rel_dir / "package.json")
        for section in ("dependencies", "optionalDependencies"):
            pending.extend(
                _workspace_dependency_names(
                    _string_map(
                        package_json, section, context=f"{rel_dir}/package.json"
                    )
                )
                - selected
            )
    return sorted(workspace_packages[name] for name in selected)


def resolve_catalog_dependencies(
    dependencies: JsonStringMap,
    catalog: JsonStringMap,
    *,
    label: str,
) -> JsonStringMap:
    """Resolve ``catalog:`` dependency specs using the workspace catalog."""
    resolved: JsonStringMap = {}
    for name, spec in dependencies.items():
        if not spec.startswith("catalog:"):
            resolved[name] = spec
            continue

        lookup_key = spec.removeprefix("catalog:").strip() or name
        try:
            resolved[name] = catalog[lookup_key]
        except KeyError as exc:
            msg = (
                f"Unable to resolve {spec!r} for {label} dependency {name!r}; "
                f"expected key {lookup_key!r} in the workspace catalog"
            )
            raise RuntimeError(msg) from exc
    return resolved


def build_runtime_manifest(
    source_root: Path,
    *,
    electron_builder_version: str | None = None,
    commit_hash: str | None = None,
    include_desktop_runtime: bool = True,
) -> JsonObject:
    """Build the packaged runtime manifest from the upstream workspace."""
    root_package = _load_json(source_root / "package.json")
    server_package = _load_json(source_root / "apps/server/package.json")
    desktop_package = _load_json(source_root / "apps/desktop/package.json")

    workspaces, workspace_overrides = _workspace_metadata(source_root, root_package)
    catalog = _string_map(workspaces, "catalog", context="workspace metadata")
    workspace_packages = _workspace_package_map(
        source_root,
        workspaces,
        context="workspace metadata",
    )

    server_dependencies = resolve_catalog_dependencies(
        _string_map(server_package, "dependencies", context="apps/server/package.json"),
        catalog,
        label="apps/server",
    )
    desktop_runtime_dependencies = (
        resolve_catalog_dependencies(
            {
                name: spec
                for name, spec in _string_map(
                    desktop_package,
                    "dependencies",
                    context="apps/desktop/package.json",
                ).items()
                if name != "electron"
            },
            catalog,
            label="apps/desktop",
        )
        if include_desktop_runtime
        else {}
    )
    runtime_dependencies = server_dependencies | desktop_runtime_dependencies
    runtime_workspace_dirs = _runtime_workspace_dirs(
        source_root,
        workspace_packages,
        runtime_dependencies,
    )
    overrides = resolve_catalog_dependencies(
        workspace_overrides,
        catalog,
        label="workspace overrides",
    )

    version = _require_string(
        server_package, "version", context="apps/server/package.json"
    )

    payload: JsonObject = {
        "name": "t3code",
        "version": version,
        "buildVersion": version,
        "private": True,
        "description": "T3 Code desktop runtime",
        "author": "T3 Tools",
        "main": "apps/desktop/dist-electron/main.cjs",
        "dependencies": runtime_dependencies,
        "overrides": overrides,
    }
    if runtime_workspace_dirs:
        payload["workspaces"] = {
            "packages": runtime_workspace_dirs,
            "catalog": catalog,
        }
    if electron_builder_version:
        payload["devDependencies"] = {"electron-builder": electron_builder_version}
    if commit_hash:
        payload["t3codeCommitHash"] = commit_hash
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_root",
        type=Path,
        help="Path to the upstream t3code workspace root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the rendered package.json.",
    )
    parser.add_argument(
        "--electron-builder-version",
        help="Optional electron-builder version to include in devDependencies.",
    )
    parser.add_argument(
        "--commit-hash",
        help="Optional git revision metadata to bake into the runtime manifest.",
    )
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Omit desktop runtime dependencies for the standalone server CLI.",
    )
    return parser.parse_args()


def main() -> None:
    """Render the runtime manifest JSON for the packaged desktop app."""
    args = _parse_args()
    payload = build_runtime_manifest(
        args.source_root.resolve(),
        electron_builder_version=args.electron_builder_version,
        commit_hash=args.commit_hash,
        include_desktop_runtime=not args.server_only,
    )
    args.output.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
