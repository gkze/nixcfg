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


def _write_tree(path: Path, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("payload", encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def test_clean_unlinks_desktop_node_modules_without_following(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    """The Darwin sandbox dies if cleanup walks desktop node_modules -> root."""
    source = tmp_path / "source"
    root_modules = source / "node_modules"
    desktop_modules = source / "apps/emdash-desktop/node_modules"
    ui_types = source / "packages/ui/node_modules/@types/node/index.d.ts"
    core_esbuild = source / "packages/core/node_modules/esbuild/lib/main.js"
    keep = source / "apps/emdash-desktop/dist/mac-arm64/Emdash.app/Contents/Info.plist"
    foreign_root = tmp_path / "foreign"
    foreign_modules = foreign_root / "node_modules/kept/package.json"
    _write_tree(root_modules / "@emdash/ui/package.json")
    _write_tree(ui_types)
    _write_tree(core_esbuild)
    _write_tree(keep)
    _write_tree(foreign_modules)
    desktop_modules.parent.mkdir(parents=True)
    desktop_modules.symlink_to(Path("../../node_modules"), target_is_directory=True)
    (source / "apps/workspace-server").mkdir(parents=True)
    (source / "apps/workspace-server/node_modules").symlink_to(
        foreign_root / "node_modules",
        target_is_directory=True,
    )
    (source / "vendor").mkdir()
    (source / "vendor/skip-me").symlink_to(foreign_root, target_is_directory=True)
    (source / "apps/emdash-desktop/node_modules_sentinel").write_text(
        "keep",
        encoding="utf-8",
    )
    file_named = source / "tools/node_modules"
    file_named.parent.mkdir()
    file_named.write_text("file", encoding="utf-8")
    readonly_dir = source / "packages/wire/node_modules"
    readonly = readonly_dir / "locked.txt"
    _write_tree(readonly, mode=0o444)
    readonly_dir.chmod(0o555)

    staging_module.main(["clean", str(source)])
    staging_module.clean_build_node_modules(source)

    assert not root_modules.exists()
    assert not desktop_modules.exists()
    assert not ui_types.exists()
    assert not core_esbuild.exists()
    assert not file_named.exists()
    assert not readonly.exists()
    assert keep.exists()
    assert foreign_modules.exists()
    assert (source / "apps/emdash-desktop/node_modules_sentinel").read_text(
        encoding="utf-8"
    ) == "keep"


def test_clean_rejects_extra_args_and_missing_staging_args(
    staging_module: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(SystemExit) as clean_error:
        staging_module.main(["clean", str(source), str(tmp_path / "node_modules")])
    assert clean_error.value.code == 2
    assert "clean takes only source_root" in capsys.readouterr().err
    with pytest.raises(SystemExit) as clean_both_error:
        staging_module.main(["clean", str(source), "node_modules", "paths"])
    assert clean_both_error.value.code == 2
    assert "clean takes only source_root" in capsys.readouterr().err
    with pytest.raises(SystemExit) as link_error:
        staging_module.main(["link", str(source)])
    assert link_error.value.code == 2
    assert "require node_modules and path_list" in capsys.readouterr().err
    with pytest.raises(SystemExit) as copy_error:
        staging_module.main(["copy", str(source), str(tmp_path / "node_modules")])
    assert copy_error.value.code == 2
    assert "require node_modules and path_list" in capsys.readouterr().err


def test_clean_requires_an_existing_source_root(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        staging_module.clean_build_node_modules(tmp_path / "missing")


def test_retry_readonly_reraises_non_permission_errors(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    path = tmp_path / "gone"
    with pytest.raises(FileNotFoundError, match="gone"):
        staging_module._retry_readonly(Path.unlink, str(path), FileNotFoundError("gone"))


def test_retry_readonly_clears_permission_and_retries(
    staging_module: ModuleType,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "node_modules"
    directory.mkdir()
    locked = directory / "locked.txt"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0o444)
    directory.chmod(0o555)

    def remove(target: str) -> None:
        Path(target).unlink()

    staging_module._retry_readonly(remove, str(locked), PermissionError("locked"))
    assert not locked.exists()
