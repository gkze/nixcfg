"""Tests for logical updater coverage across packages and overlays."""

import annotationlib
import inspect
from dataclasses import fields
from pathlib import Path

import pytest

from lib.update import crate2nix as update_crate2nix
from lib.update import source_runner
from lib.update.events import StatusKind
from lib.update.paths import REPO_ROOT, package_file_map_in
from lib.update.persistence import planned_update_paths
from lib.update.refs import get_flake_inputs_with_refs
from lib.update.surfaces import (
    UPDATE_SURFACE_ALIASES,
    UPDATE_SURFACE_EXEMPTIONS,
    canonical_update_surface_name,
    discover_update_surface_names,
    validate_update_surface_coverage,
)
from lib.update.updaters import Updater, ensure_updaters_loaded
from lib.update.updaters import metadata as updater_metadata


def test_surface_alias_and_exemption_contracts() -> None:
    """Keep explicit coverage exceptions small and intentional."""
    assert UPDATE_SURFACE_ALIASES == {
        "claude-code-url-handler": "claude-code",
        "opencode-desktop-dev": "opencode-desktop",
    }
    assert {
        "codex-v8-native",
        "goose-cli-v8-native",
        "nix",
        "nix-direnv",
        "nix-prefetch-git",
    } == UPDATE_SURFACE_EXEMPTIONS
    assert canonical_update_surface_name("claude-code-url-handler") == "claude-code"
    assert canonical_update_surface_name("opencode-desktop-dev") == ("opencode-desktop")


def test_update_runtime_has_no_cross_job_version_transport_surface() -> None:
    """Keep version discovery local to each isolated updater execution."""
    signature = inspect.signature(
        Updater.update_stream,
        annotation_format=annotationlib.Format.STRING,
    )
    assert "pinned_version" not in signature.parameters
    assert "pinned_version" not in {
        field.name for field in fields(source_runner.SourceTaskContext)
    }
    assert "PINNED_VERSION" not in StatusKind.__members__
    assert not hasattr(updater_metadata, "serialize_metadata")
    assert not hasattr(updater_metadata, "deserialize_metadata")
    assert all(
        not hasattr(metadata_type, "KIND")
        for metadata_type in (
            updater_metadata.AssetURLsMetadata,
            updater_metadata.DownloadUrlMetadata,
            updater_metadata.FlakeInputMetadata,
            updater_metadata.GranolaFeedMetadata,
            updater_metadata.GitHubRawFileMetadata,
            updater_metadata.GitHubReleaseMetadata,
            updater_metadata.NoMetadata,
            updater_metadata.PlatformAPIMetadata,
            updater_metadata.ReleasePayloadMetadata,
        )
    )


def test_discover_update_surface_names_finds_repo_surfaces() -> None:
    """Discover logical update surfaces across directory and flat package layouts."""
    surfaces = discover_update_surface_names()
    assert "codex-desktop" in surfaces
    assert "chatgpt" not in surfaces
    assert "zed-editor-nightly" in surfaces
    assert "codex-v8" in surfaces
    assert "opencode-desktop-dev" in surfaces
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


def test_every_repo_source_has_a_plannable_transaction_destination() -> None:
    """Plan every real source without imposing package-directory uniqueness."""
    source_paths = package_file_map_in(REPO_ROOT, "sources.json")
    updater_paths = package_file_map_in(REPO_ROOT, "updater.py")
    updaters = ensure_updaters_loaded()

    assert set(updaters) == set(source_paths) == set(updater_paths)

    expected = {path.resolve() for path in source_paths.values()}
    for name, updater in updaters.items():
        owner_dir = updater_paths[name].parent
        expected.update(
            (owner_dir / relative).resolve()
            for relative in updater.get_generated_artifact_files()
        )
    for target in update_crate2nix.TARGETS.values():
        expected.update((REPO_ROOT / path).resolve() for path in target.artifact_paths)

    assert set(planned_update_paths(sorted(updaters), updaters)) == expected


def test_validate_update_surface_coverage_reports_missing_alias_target(
    tmp_path: Path,
) -> None:
    """Report unresolved canonical targets with the alias mapping included."""
    package_dir = tmp_path / "packages" / "opencode-desktop-dev"
    package_dir.mkdir(parents=True)
    (package_dir / "default.nix").write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="opencode-desktop-dev -> opencode-desktop",
    ):
        validate_update_surface_coverage(
            updater_names=set(),
            ref_input_names=set(),
            root=tmp_path,
        )
