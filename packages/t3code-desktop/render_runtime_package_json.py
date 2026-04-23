"""Render the minimal runtime ``package.json`` for the packaged T3 Code desktop app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

JsonObject = dict[str, Any]
JsonStringMap = dict[str, str]


def _load_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected a JSON object in {path}"
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
) -> JsonObject:
    """Build the packaged desktop runtime manifest from the upstream workspace."""
    root_package = _load_json(source_root / "package.json")
    server_package = _load_json(source_root / "apps/server/package.json")
    desktop_package = _load_json(source_root / "apps/desktop/package.json")

    workspaces = _require_object(
        root_package, "workspaces", context="root package.json"
    )
    catalog = _string_map(workspaces, "catalog", context="root package.json workspaces")

    server_dependencies = resolve_catalog_dependencies(
        _string_map(server_package, "dependencies", context="apps/server/package.json"),
        catalog,
        label="apps/server",
    )
    desktop_runtime_dependencies = resolve_catalog_dependencies(
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
    overrides = resolve_catalog_dependencies(
        _string_map(root_package, "overrides", context="root package.json"),
        catalog,
        label="root overrides",
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
        "dependencies": server_dependencies | desktop_runtime_dependencies,
        "overrides": overrides,
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
    return parser.parse_args()


def main() -> None:
    """Render the runtime manifest JSON for the packaged desktop app."""
    args = _parse_args()
    payload = build_runtime_manifest(
        args.source_root.resolve(),
        electron_builder_version=args.electron_builder_version,
        commit_hash=args.commit_hash,
    )
    args.output.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
