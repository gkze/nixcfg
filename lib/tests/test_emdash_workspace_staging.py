"""Behavioral tests for Emdash workspace package staging."""

import json
from pathlib import Path
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module


@pytest.fixture(scope="module")
def staging_module() -> ModuleType:
    """Load the staging helper from the package source tree."""
    return load_repo_module(
        "packages/emdash/stage_workspace_packages.py",
        "emdash_workspace_staging_test",
    )


def _write_package(path: Path, name: object, *, content: str = "built") -> None:
    path.mkdir(parents=True)
    (path / "package.json").write_text(
        json.dumps({"name": name}),
        encoding="utf-8",
    )
    (path / "artifact.txt").write_text(content, encoding="utf-8")


def test_staging_uses_pnpm_paths_and_manifest_package_names(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    """Nested paths and renamed package identities survive both staging phases."""
    source_root = tmp_path / "source"
    nested_source = source_root / "components/deep/layout/directory-basename"
    unscoped_source = source_root / "apps/another-layout"
    _write_package(nested_source, "@renamed/canonical-name")
    _write_package(unscoped_source, "standalone-package")
    path_list = tmp_path / "workspace-paths"
    path_list.write_text(
        f"{nested_source}\napps/another-layout\n",
        encoding="utf-8",
    )
    node_modules = tmp_path / "node_modules"
    stale_destination = node_modules / "@renamed/canonical-name"
    stale_destination.mkdir(parents=True)
    (stale_destination / "stale").write_text("old", encoding="utf-8")

    packages = staging_module.workspace_packages(source_root, path_list)
    assert [(package.source, package.name) for package in packages] == [
        (nested_source, "@renamed/canonical-name"),
        (unscoped_source, "standalone-package"),
    ]

    staging_module.stage_workspace_packages(packages, node_modules, mode="link")
    scoped_destination = node_modules / "@renamed/canonical-name"
    unscoped_destination = node_modules / "standalone-package"
    assert scoped_destination.is_symlink()
    assert scoped_destination.resolve() == nested_source
    assert unscoped_destination.resolve() == unscoped_source
    assert not (node_modules / "@emdash/directory-basename").exists()

    (nested_source / "artifact.txt").write_text("rebuilt", encoding="utf-8")
    staging_module.stage_workspace_packages(packages, node_modules, mode="copy")
    assert not scoped_destination.is_symlink()
    assert (scoped_destination / "artifact.txt").read_text(
        encoding="utf-8"
    ) == "rebuilt"
    assert (unscoped_destination / "artifact.txt").read_text(
        encoding="utf-8"
    ) == "built"


def test_command_entrypoint_stages_selected_packages(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    package = source_root / "custom/location"
    _write_package(package, "@emdash/from-manifest")
    path_list = tmp_path / "workspace-paths"
    path_list.write_text(f"{package}\n", encoding="utf-8")
    node_modules = tmp_path / "node_modules"

    staging_module.main([
        "link",
        str(source_root),
        str(node_modules),
        str(path_list),
    ])

    assert (node_modules / "@emdash/from-manifest").resolve() == package


@pytest.mark.parametrize(
    ("payload", "error_type", "message"),
    [
        ([], TypeError, "not an object"),
        ({}, TypeError, "no package name"),
        ({"name": "@scope"}, RuntimeError, "invalid package name"),
        ({"name": "../escape"}, RuntimeError, "invalid package name"),
        ({"name": "Uppercase"}, RuntimeError, "invalid package name"),
    ],
)
def test_package_identity_rejects_malformed_manifests(
    staging_module: ModuleType,
    tmp_path: Path,
    payload: object,
    error_type: type[Exception],
    message: str,
) -> None:
    manifest = tmp_path / "package.json"
    with pytest.raises(error_type, match=message):
        staging_module._package_name(payload, manifest=manifest)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{broken", "not valid UTF-8 JSON"),
        (b"\xff", "not valid UTF-8 JSON"),
    ],
)
def test_manifest_reader_rejects_invalid_content(
    staging_module: ModuleType,
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    manifest = tmp_path / "package.json"
    manifest.write_bytes(payload)
    with pytest.raises(RuntimeError, match=message):
        staging_module._read_manifest(manifest)


def test_manifest_reader_rejects_absent_or_oversized_files(
    staging_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(RuntimeError, match="has no manifest"):
        staging_module._read_manifest(missing)

    manifest = tmp_path / "large.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(staging_module, "_MAX_MANIFEST_BYTES", 1)
    with pytest.raises(RuntimeError, match="exceeds 1 bytes"):
        staging_module._read_manifest(manifest)


def test_workspace_path_list_requires_utf8_nonempty_existing_in_tree_paths(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    path_list = tmp_path / "workspace-paths"

    path_list.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="path list is not UTF-8"):
        staging_module.workspace_packages(source_root, path_list)

    path_list.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no workspace package dependencies"):
        staging_module.workspace_packages(source_root, path_list)

    path_list.write_text("missing\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="path does not exist"):
        staging_module.workspace_packages(source_root, path_list)

    outside = tmp_path / "outside"
    _write_package(outside, "outside")
    path_list.write_text(f"{outside}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="escapes the source tree"):
        staging_module.workspace_packages(source_root, path_list)

    plain_file = source_root / "plain-file"
    plain_file.write_text("not a package", encoding="utf-8")
    path_list.write_text(f"{plain_file}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="escapes the source tree"):
        staging_module.workspace_packages(source_root, path_list)


def test_workspace_path_list_rejects_empty_duplicate_paths_and_names(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    first = source_root / "first"
    second = source_root / "second"
    _write_package(first, "@emdash/shared")
    _write_package(second, "@emdash/shared")
    path_list = tmp_path / "workspace-paths"

    path_list.write_text(f"{first}\n\n{second}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="contains an empty path"):
        staging_module.workspace_packages(source_root, path_list)

    path_list.write_text(f"{first}\n{first}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="listed more than once"):
        staging_module.workspace_packages(source_root, path_list)

    path_list.write_text(f"{first}\n{second}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="package name is not unique"):
        staging_module.workspace_packages(source_root, path_list)


def test_staging_rejects_unknown_mode_and_replaces_existing_file(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source/package"
    _write_package(source, "package")
    package = staging_module.WorkspacePackage(source=source, name="package")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    destination = node_modules / "package"
    destination.write_text("stale", encoding="utf-8")

    staging_module.stage_workspace_packages((package,), node_modules, mode="link")
    assert destination.is_symlink()

    with pytest.raises(ValueError, match="Unsupported Emdash workspace staging mode"):
        staging_module.stage_workspace_packages((package,), node_modules, mode="move")
