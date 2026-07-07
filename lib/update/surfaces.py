"""Update surface discovery and coverage helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from lib.update.paths import REPO_ROOT, package_file_names_in

if TYPE_CHECKING:
    from pathlib import Path

UPDATE_SURFACE_ALIASES: dict[str, str] = {
    "opencode-desktop-dev": "opencode-desktop",
}

UPDATE_SURFACE_EXEMPTIONS = frozenset({
    "electron-runtimes",  # aggregate/cache package, not an external update target
    "nix",
})

_SURFACE_FILES = ("default.nix", "sources.json", "updater.py")


def discover_update_surface_names(root: Path = REPO_ROOT) -> set[str]:
    """Return logical package/overlay names that participate in update flows."""
    names: set[str] = set()
    for filename in _SURFACE_FILES:
        for name in package_file_names_in(root, filename):
            if name.startswith(("_", ".")):
                continue
            names.add(name)
    return names


def canonical_update_surface_name(name: str) -> str:
    """Return the updater/ref target that covers *name*."""
    return UPDATE_SURFACE_ALIASES.get(name, name)


def validate_update_surface_coverage(
    *,
    updater_names: set[str],
    ref_input_names: set[str],
    root: Path = REPO_ROOT,
) -> None:
    """Ensure every discovered update surface resolves to a known target."""
    available_targets = updater_names | ref_input_names
    missing: list[str] = []

    for name in sorted(discover_update_surface_names(root)):
        if name in UPDATE_SURFACE_EXEMPTIONS:
            continue
        target = canonical_update_surface_name(name)
        if target in available_targets:
            continue
        missing.append(name if target == name else f"{name} -> {target}")

    if not missing:
        return

    lines = ["Update surface coverage mismatch detected:"]
    lines.extend(f"- Missing updater/ref target for {item}" for item in missing)
    raise RuntimeError("\n".join(lines))


def validate_repo_update_surface_coverage() -> None:
    """Validate coverage for the current repository state."""
    refs_module = importlib.import_module("lib.update.refs")
    updaters_module = importlib.import_module("lib.update.updaters")

    validate_update_surface_coverage(
        updater_names=set(updaters_module.ensure_updaters_loaded()),
        ref_input_names={ref.name for ref in refs_module.get_flake_inputs_with_refs()},
    )


__all__ = [
    "UPDATE_SURFACE_ALIASES",
    "UPDATE_SURFACE_EXEMPTIONS",
    "canonical_update_surface_name",
    "discover_update_surface_names",
    "validate_repo_update_surface_coverage",
    "validate_update_surface_coverage",
]
