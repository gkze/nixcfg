"""Tests for logical updater coverage across packages and overlays."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.update.refs import get_flake_inputs_with_refs
from lib.update.surfaces import (
    UPDATE_SURFACE_ALIASES,
    UPDATE_SURFACE_EXEMPTIONS,
    canonical_update_surface_name,
    discover_update_surface_names,
    validate_update_surface_coverage,
)
from lib.update.updaters import ensure_updaters_loaded


def test_surface_alias_and_exemption_contracts() -> None:
    """Keep explicit coverage exceptions small and intentional."""
    assert UPDATE_SURFACE_ALIASES == {
        "opencode-desktop-electron-dev": "opencode-desktop-electron",
    }
    assert {"electron-runtimes", "nix"} == UPDATE_SURFACE_EXEMPTIONS
    assert canonical_update_surface_name("opencode-desktop-electron-dev") == (
        "opencode-desktop-electron"
    )


def test_discover_update_surface_names_finds_repo_surfaces() -> None:
    """Discover logical update surfaces across directory and flat package layouts."""
    surfaces = discover_update_surface_names()
    assert "zed-editor-nightly" in surfaces
    assert "codex-v8" in surfaces
    assert "opencode-desktop-electron-dev" in surfaces
    assert "nix" in surfaces
    assert "zoom-us" in surfaces


def test_discover_update_surface_names_supports_flat_package_files(
    tmp_path: Path,
) -> None:
    """Flat ``<name>.sources.json`` surfaces should be discovered like directory-backed ones."""
    overlays_dir = tmp_path / "overlays"
    overlays_dir.mkdir(parents=True)
    (overlays_dir / "zoom-us.sources.json").write_text("{}\n", encoding="utf-8")

    assert discover_update_surface_names(tmp_path) == {"zoom-us"}


def test_discover_update_surface_names_skips_hidden_flat_files_and_non_files(
    tmp_path: Path,
) -> None:
    """Ignore hidden flat entries and paths that are neither files nor directories."""
    overlays_dir = tmp_path / "overlays"
    overlays_dir.mkdir(parents=True)
    (overlays_dir / ".hidden.sources.json").write_text("{}\n", encoding="utf-8")
    (overlays_dir / "visible.sources.json").write_text("{}\n", encoding="utf-8")
    (overlays_dir / "broken-link").symlink_to(tmp_path / "missing-target")

    assert discover_update_surface_names(tmp_path) == {"visible"}


def test_validate_update_surface_coverage_accepts_current_repo() -> None:
    """The current repo should map every surface to an updater or flake ref."""
    validate_update_surface_coverage(
        updater_names=set(ensure_updaters_loaded()),
        ref_input_names={ref.name for ref in get_flake_inputs_with_refs()},
    )


def test_validate_update_surface_coverage_reports_missing_alias_target(
    tmp_path: Path,
) -> None:
    """Report unresolved canonical targets with the alias mapping included."""
    package_dir = tmp_path / "packages" / "opencode-desktop-electron-dev"
    package_dir.mkdir(parents=True)
    (package_dir / "default.nix").write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="opencode-desktop-electron-dev -> opencode-desktop-electron",
    ):
        validate_update_surface_coverage(
            updater_names=set(),
            ref_input_names=set(),
            root=tmp_path,
        )
