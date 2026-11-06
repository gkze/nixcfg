"""Render the minimal runtime ``package.json`` for the packaged T3 Code desktop app."""

import argparse
import json
import sys
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

    workspace_value = root_package.get("workspaces")
    root_catalog = _string_map(root_package, "catalog", context="root package.json")
    if workspace_value is None:
        workspaces = {"packages": [], "catalog": root_catalog}
    elif isinstance(workspace_value, list):
        if not all(isinstance(item, str) for item in workspace_value):
            msg = "Expected 'workspaces' to be a string list in root package.json"
            raise TypeError(msg)
        workspaces = {
            "packages": cast("list[str]", workspace_value),
            "catalog": root_catalog,
        }
    elif isinstance(workspace_value, dict):
        workspaces = dict(cast("JsonObject", workspace_value))
        if root_catalog:
            catalog = _string_map(workspaces, "catalog", context="root package.json")
            workspaces["catalog"] = catalog | root_catalog
    else:
        msg = (
            "Expected 'workspaces' to be a JSON object or string list "
            "in root package.json"
        )
        raise TypeError(msg)
    overrides = _string_map(root_package, "overrides", context="root package.json")
    return workspaces, overrides


_WORKSPACE_MEMBER_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")
_PRODUCTION_DEPENDENCY_SECTIONS = ("dependencies", "optionalDependencies")


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
        # Bun installs every workspace member's devDependencies, so a member's
        # dev-only ``workspace:*`` reference must be a member too or the
        # install cannot resolve it (Update run 39774011035).
        for section in _WORKSPACE_MEMBER_SECTIONS:
            pending.extend(
                _workspace_dependency_names(
                    _string_map(
                        package_json, section, context=f"{rel_dir}/package.json"
                    )
                )
                - selected
            )
    return sorted(workspace_packages[name] for name in selected)


def _hoist_workspace_production_dependencies(
    source_root: Path,
    workspace_packages: dict[str, str],
    dependencies: JsonStringMap,
) -> JsonStringMap:
    """Return transitive ``workspace:`` production deps Bun must link at the root.

    Bun's isolated linker links only direct dependencies of the root manifest
    at ``node_modules/<name>``. electron-builder's traversal collector
    realpaths a workspace package to its source directory, skips
    ``node_modules/.bun``, and then searches parent ``node_modules``
    directories. A production ``workspace:`` dependency that exists only on a
    workspace member is installed under ``.bun`` and the collector reports it
    missing. Nightly ``0.1.8-nightly.20261106.4247`` failed this way:
    ``@t3tools/client-runtime`` depends on
    ``@t3tools/secret-provider-definitions``, which was not a root dependency.
    """
    hoisted: JsonStringMap = {}
    seen: set[str] = set()
    pending = [
        name for name, spec in dependencies.items() if spec.startswith("workspace:")
    ]
    while pending:
        package_name = pending.pop()
        if package_name in seen:
            continue
        seen.add(package_name)
        rel_dir = workspace_packages[package_name]
        package_json = _load_json(source_root / rel_dir / "package.json")
        context = f"{rel_dir}/package.json"
        for section in _PRODUCTION_DEPENDENCY_SECTIONS:
            for name, spec in _string_map(
                package_json, section, context=context
            ).items():
                if not spec.startswith("workspace:"):
                    continue
                current = dependencies.get(name, hoisted.get(name))
                if current is None:
                    hoisted[name] = spec
                elif current != spec:
                    msg = (
                        f"Workspace dependency {name!r} is {current!r} at the "
                        f"runtime root and {spec!r} in {rel_dir}"
                    )
                    raise RuntimeError(msg)
                pending.append(name)
    return hoisted


def missing_hoisted_production_dependencies(root: Path) -> list[str]:
    """Return production deps electron-builder cannot resolve from ``root``.

    Dependencies of a package outside Bun's store must be linked at
    ``node_modules/<name>``, because that is the only place the collector
    searches after realpath leaves ``node_modules/.bun``. A package already
    inside the store can see its own nested dependencies, so a missing root
    link there is not a packaging failure. Missing optional dependencies are
    skipped, matching the collector.
    """
    missing: set[str] = set()
    seen: set[Path] = set()
    pending: list[tuple[Path, str]] = [(root, "package.json")]
    while pending:
        package_dir, context = pending.pop()
        resolved = package_dir.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        package_json = _load_json(resolved / "package.json")
        inside_bun_store = ".bun" in resolved.parts
        for section in _PRODUCTION_DEPENDENCY_SECTIONS:
            optional = section == "optionalDependencies"
            declared = _string_map(package_json, section, context=context)
            for name in declared:
                linked = root / "node_modules" / name
                if (linked / "package.json").is_file():
                    pending.append((linked, f"node_modules/{name}/package.json"))
                    continue
                if optional or inside_bun_store:
                    continue
                missing.add(name)
    return sorted(missing)


def _runtime_workspace_external_dependencies(
    source_root: Path,
    workspace_dirs: list[str],
    workspace_packages: dict[str, str],
    catalog: JsonStringMap,
) -> JsonStringMap:
    dependencies: JsonStringMap = {}
    workspace_names = set(workspace_packages)
    for rel_dir in workspace_dirs:
        package_json = _load_json(source_root / rel_dir / "package.json")
        for section in _PRODUCTION_DEPENDENCY_SECTIONS:
            package_dependencies = resolve_catalog_dependencies(
                {
                    name: spec
                    for name, spec in _string_map(
                        package_json, section, context=f"{rel_dir}/package.json"
                    ).items()
                    if name not in workspace_names
                },
                catalog,
                label=rel_dir,
            )
            dependencies.update(package_dependencies)
    return dependencies


def _runtime_overrides(
    overrides: JsonStringMap,
    dependencies: JsonStringMap,
) -> JsonStringMap:
    runtime_dependency_names = set(dependencies)
    return {
        name: spec
        for name, spec in overrides.items()
        if name.split(">", maxsplit=1)[0] in runtime_dependency_names
    }


def _catalog_entry_name(name: str) -> str:
    """Return the package a bare ``catalog:`` spec names.

    A pnpm override key such as ``@opencode/protocol>effect`` targets the
    package after the last ``>``, optionally with a version selector, and that
    package name is the catalog key.
    """
    target = name.rsplit(">", maxsplit=1)[-1]
    package, separator, _selector = target.rpartition("@")
    return package if separator and package else target


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

        lookup_key = spec.removeprefix("catalog:").strip() or _catalog_entry_name(name)
        try:
            resolved[name] = catalog[lookup_key]
        except KeyError as exc:
            msg = (
                f"Unable to resolve {spec!r} for {label} dependency {name!r}; "
                f"expected key {lookup_key!r} in the workspace catalog"
            )
            raise RuntimeError(msg) from exc
    return resolved


def _runtime_entry_point(desktop_package: JsonObject) -> str:
    """Return the packaged Electron entry point inside the build root.

    Upstream moves this between ``main.cjs`` and ``boot.cjs`` across releases, so
    the value comes from ``apps/desktop/package.json`` instead of being assumed.
    """
    main = desktop_package.get("main")
    if not isinstance(main, str) or not main:
        main = "dist-electron/main.cjs"
    return f"apps/desktop/{main}"


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
    # The server bundle does not run electron-builder, and its node_modules
    # derivation strips workspace links. Hoisting those links into the server
    # manifest would rewrite its lock with no consumer.
    if include_desktop_runtime:
        runtime_dependencies |= _hoist_workspace_production_dependencies(
            source_root,
            workspace_packages,
            runtime_dependencies,
        )
    runtime_dependencies |= _runtime_workspace_external_dependencies(
        source_root,
        runtime_workspace_dirs,
        workspace_packages,
        catalog,
    )
    overrides = resolve_catalog_dependencies(
        _runtime_overrides(workspace_overrides, runtime_dependencies),
        catalog,
        label="workspace overrides",
    )

    resolved_version = _require_string(
        server_package, "version", context="apps/server/package.json"
    )

    payload: JsonObject = {
        "name": "t3code",
        "version": resolved_version,
        "buildVersion": resolved_version,
        "private": True,
        "description": "T3 Code desktop runtime",
        "author": "T3 Tools",
        "main": _runtime_entry_point(desktop_package),
        "dependencies": runtime_dependencies,
        "overrides": overrides,
    }
    if runtime_workspace_dirs:
        payload["workspaces"] = {
            "packages": runtime_workspace_dirs,
            "catalog": catalog,
        }
    if electron_builder_version:
        payload["devDependencies"] = {
            "electron-builder": electron_builder_version,
            "electron-builder-squirrel-windows": electron_builder_version,
        }
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
    parser.add_argument(
        "--check-hoisted-production-deps",
        action="store_true",
        help=(
            "Check that electron-builder can resolve production dependencies "
            "from this installed runtime root, and do not write a manifest."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Render the runtime manifest, or check hoisted production links."""
    args = _parse_args()
    if args.check_hoisted_production_deps:
        missing = missing_hoisted_production_dependencies(args.source_root.resolve())
        if missing:
            sys.stderr.write(
                "electron-builder cannot see production dependencies at "
                f"node_modules: {', '.join(missing)}\n"
            )
            raise SystemExit(1)
        return
    if args.output is None:
        msg = "--output is required to render the runtime manifest"
        raise SystemExit(msg)
    payload = build_runtime_manifest(
        args.source_root.resolve(),
        electron_builder_version=args.electron_builder_version,
        commit_hash=args.commit_hash,
        include_desktop_runtime=not args.server_only,
    )
    args.output.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
