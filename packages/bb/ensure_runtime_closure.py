"""Root bb's SQLite loader and logger closure in the desktop package.

electron-builder can omit hoisted transitive dependencies. The packaged app
still needs ``bindings`` 1.5.0 and ``file-uri-to-path`` 1.0.0 to load
better-sqlite3, and ``wrappy`` 1.0.2 to load pino-pretty through once. Those
exact versions stay in the pnpm lock as transitive packages; desktop
dependency order and the neighboring specifiers move between releases.

A unified diff anchors on those neighbors, so 0.49.0 fails patchPhase while
the closure itself is unchanged. This rewrite inserts the three roots from
the lock's existing resolutions. It does not invent a version the lock does
not already contain.

The manifest is JSON, so the standard library owns that edit. The lock edit
is only the ``apps/desktop`` dependency block: a YAML dump would rewrite the
rest of the frozen lock, and the nix build's Python does not ship PyYAML.
"""

import argparse
import json
from pathlib import Path
from typing import cast

RUNTIME_ROOTS: tuple[tuple[str, str], ...] = (
    ("bindings", "1.5.0"),
    ("file-uri-to-path", "1.0.0"),
    ("wrappy", "1.0.2"),
)
_DESKTOP_DEPENDENCIES_HEADER = "  apps/desktop:\n    dependencies:\n"
_ENTRY_INDENT = "      "


class RuntimeClosureError(ValueError):
    """Raised when bb's desktop manifest and lock cannot root the closure."""


def _rooted_dependencies(dependencies: dict[str, object]) -> dict[str, object]:
    ordered_keys = list(dependencies)
    roots = dict(RUNTIME_ROOTS)
    for name, version in RUNTIME_ROOTS:
        if name in dependencies:
            current = dependencies[name]
            if current != version:
                msg = (
                    f"desktop dependency {name} is {current!r}, "
                    f"not runtime root {version}"
                )
                raise RuntimeClosureError(msg)
            continue
        index = next(
            (position for position, key in enumerate(ordered_keys) if key > name),
            len(ordered_keys),
        )
        ordered_keys.insert(index, name)
    return {
        key: dependencies[key] if key in dependencies else roots[key]
        for key in ordered_keys
    }


def rooted_manifest(manifest: object) -> dict[str, object]:
    """Return the desktop manifest with the runtime roots declared."""
    if not isinstance(manifest, dict):
        msg = "desktop package.json is not a JSON object"
        raise RuntimeClosureError(msg)
    typed = cast("dict[str, object]", manifest)
    dependencies = typed.get("dependencies")
    if not isinstance(dependencies, dict):
        msg = "desktop package.json dependencies must be a JSON object"
        raise RuntimeClosureError(msg)
    updated = dict(typed)
    updated["dependencies"] = _rooted_dependencies(
        cast("dict[str, object]", dependencies),
    )
    return updated


def _dependency_body_span(lock_text: str) -> tuple[int, int]:
    start = lock_text.find(_DESKTOP_DEPENDENCIES_HEADER)
    if start < 0:
        msg = "pnpm lock has no apps/desktop dependencies"
        raise RuntimeClosureError(msg)
    if lock_text.find(_DESKTOP_DEPENDENCIES_HEADER, start + 1) >= 0:
        msg = "pnpm lock has multiple apps/desktop dependency sections"
        raise RuntimeClosureError(msg)
    body_start = start + len(_DESKTOP_DEPENDENCIES_HEADER)
    cursor = body_start
    while cursor < len(lock_text):
        newline = lock_text.find("\n", cursor)
        line_end = len(lock_text) if newline < 0 else newline + 1
        line = lock_text[cursor:line_end]
        if _dependency_section_ends(line):
            return body_start, cursor
        cursor = line_end
    msg = "apps/desktop dependencies section does not end"
    raise RuntimeClosureError(msg)


def _dependency_section_ends(line: str) -> bool:
    if line.startswith(_ENTRY_INDENT):
        return False
    sibling_key = line.startswith("    ")
    next_importer = line.startswith("  ") and not line.startswith("   ")
    if not sibling_key and not next_importer:
        return False
    return line.strip().endswith(":")


def _entry_name(header: str) -> str:
    raw = header.removeprefix(_ENTRY_INDENT).removesuffix("\n").removesuffix(":")
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    if not raw:
        msg = "desktop dependency name is empty"
        raise RuntimeClosureError(msg)
    return raw


def _split_dependency_entries(body: str) -> list[tuple[str, str]]:
    if body == "":
        return []
    entries: list[tuple[str, str]] = []
    current_name: str | None = None
    current: list[str] = []
    for line in body.splitlines(keepends=True):
        if line.startswith(_ENTRY_INDENT) and not line.startswith(f"{_ENTRY_INDENT} "):
            if current_name is not None:
                entries.append((current_name, "".join(current)))
            current_name = _entry_name(line)
            current = [line]
            continue
        if current_name is None:
            msg = "apps/desktop dependencies have a preamble before the first entry"
            raise RuntimeClosureError(msg)
        current.append(line)
    # A non-empty body sets current_name or raises before the loop finishes.
    entries.append((cast("str", current_name), "".join(current)))
    return entries


def _field(block: str, name: str, field: str) -> str:
    prefix = f"        {field}: "
    for line in block.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    msg = f"desktop lock entry {name} is missing {field}"
    raise RuntimeClosureError(msg)


def _require_existing_resolution(lock_text: str, name: str, version: str) -> None:
    needle = f"\n  {name}@{version}:\n"
    if needle not in lock_text:
        msg = (
            f"pnpm lock does not resolve {name}@{version}; "
            "refusing to invent a runtime-closure root"
        )
        raise RuntimeClosureError(msg)


def _root_entry(name: str, version: str) -> str:
    return f"{_ENTRY_INDENT}{name}:\n        specifier: {version}\n        version: {version}\n"


def ensure_desktop_lock(lock_text: str) -> str:
    """Return the lock with desktop importer roots for the runtime closure."""
    body_start, body_end = _dependency_body_span(lock_text)
    entries = _split_dependency_entries(lock_text[body_start:body_end])
    seen: set[str] = set()
    for name, _block in entries:
        if name in seen:
            msg = f"desktop lock lists {name} more than once"
            raise RuntimeClosureError(msg)
        seen.add(name)
    inserted = False
    for name, version in RUNTIME_ROOTS:
        _require_existing_resolution(lock_text, name, version)
        if name in seen:
            block = next(block for entry_name, block in entries if entry_name == name)
            specifier = _field(block, name, "specifier")
            resolved = _field(block, name, "version")
            if specifier != version or resolved != version:
                msg = (
                    f"desktop lock entry {name} resolves {specifier} -> {resolved}, "
                    f"not runtime root {version}"
                )
                raise RuntimeClosureError(msg)
            continue
        index = next(
            (
                position
                for position, (entry_name, _) in enumerate(entries)
                if entry_name > name
            ),
            len(entries),
        )
        entries.insert(index, (name, _root_entry(name, version)))
        seen.add(name)
        inserted = True
    if not inserted:
        return lock_text
    body = "".join(block for _name, block in entries)
    return f"{lock_text[:body_start]}{body}{lock_text[body_end:]}"


def assert_runtime_closure_applicable(manifest: object, lock_text: str) -> None:
    """Fail unless this release can still root the packaged runtime closure."""
    rooted_manifest(manifest)
    ensure_desktop_lock(lock_text)


def _read_manifest(path: Path) -> tuple[str, object]:
    if not path.is_file():
        msg = "desktop package.json is missing"
        raise RuntimeClosureError(msg)
    text = path.read_text(encoding="utf-8")
    try:
        return text, json.loads(text)
    except json.JSONDecodeError as exc:
        msg = "desktop package.json is not JSON"
        raise RuntimeClosureError(msg) from exc


def ensure_runtime_closure(source: Path) -> None:
    """Insert the runtime roots into a bb source tree when the lock allows it."""
    manifest_path = source / "apps" / "desktop" / "package.json"
    lock_path = source / "pnpm-lock.yaml"
    if not lock_path.is_file():
        msg = "pnpm-lock.yaml is missing"
        raise RuntimeClosureError(msg)
    manifest_text, manifest = _read_manifest(manifest_path)
    rendered = (
        json.dumps(rooted_manifest(manifest), indent=2, ensure_ascii=False) + "\n"
    )
    lock_text = lock_path.read_text(encoding="utf-8")
    updated_lock = ensure_desktop_lock(lock_text)
    if rendered != manifest_text:
        manifest_path.write_text(rendered, encoding="utf-8")
    if updated_lock != lock_text:
        lock_path.write_text(updated_lock, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Root the runtime closure in the selected bb source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args(argv)
    ensure_runtime_closure(args.source)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through public main
    raise SystemExit(main())
