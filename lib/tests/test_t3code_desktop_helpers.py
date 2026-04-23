"""Unit tests for T3 Code desktop packaging helpers."""

from __future__ import annotations

import json
import plistlib
import runpy
from pathlib import Path

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module(path: str, name: str):
    return load_module_from_path(REPO_ROOT / path, name)


def test_render_runtime_package_json_resolves_catalog_dependencies(
    tmp_path: Path,
) -> None:
    """The staged runtime manifest should resolve catalog specs and omit Electron."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({
            "workspaces": {
                "catalog": {
                    "effect": "4.0.0-beta.45",
                    "@effect/platform-node": "4.0.0-beta.45",
                }
            },
            "overrides": {
                "effect": "catalog:",
            },
        }),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({
            "version": "0.0.21",
            "dependencies": {
                "effect": "catalog:",
                "open": "^10.1.0",
            },
        }),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({
            "dependencies": {
                "@effect/platform-node": "catalog:",
                "electron": "40.6.0",
                "electron-updater": "^6.6.2",
            },
        }),
        encoding="utf-8",
    )

    payload = module.build_runtime_manifest(
        source_root,
        electron_builder_version="26.8.1",
        commit_hash="abc1234",
    )

    assert payload["version"] == "0.0.21"
    assert payload["t3codeCommitHash"] == "abc1234"
    assert payload["dependencies"] == {
        "effect": "4.0.0-beta.45",
        "open": "^10.1.0",
        "@effect/platform-node": "4.0.0-beta.45",
        "electron-updater": "^6.6.2",
    }
    assert payload["devDependencies"] == {"electron-builder": "26.8.1"}
    assert payload["overrides"] == {"effect": "4.0.0-beta.45"}
    assert "electron" not in payload["dependencies"]


def test_render_runtime_package_json_rejects_unresolved_catalog_dependency(
    tmp_path: Path,
) -> None:
    """Raise a targeted error when a catalog dependency cannot be resolved."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_missing_catalog_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({"workspaces": {"catalog": {}}, "overrides": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "0.0.21", "dependencies": {"effect": "catalog:"}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {}}),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError, match="expected key 'effect' in the workspace catalog"
    ):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_rejects_invalid_root_shapes(
    tmp_path: Path,
) -> None:
    """Type-check the workspace metadata before building the runtime manifest."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_bad_shape_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({"workspaces": []}),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "0.0.21", "dependencies": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {}}),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="Expected 'workspaces' to be a JSON object"):
        module.build_runtime_manifest(source_root)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not", "an", "object"], "Expected a JSON object"),
        ({"workspaces": {"catalog": []}}, "Expected 'catalog' to be a JSON object"),
        (
            {"workspaces": {"catalog": {}}, "overrides": {"effect": 1}},
            "Expected string entries in 'overrides'",
        ),
    ],
)
def test_render_runtime_package_json_rejects_invalid_json_shapes(
    tmp_path: Path,
    payload: object,
    match: str,
) -> None:
    """Validate helper branches that reject malformed JSON payloads and maps."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_invalid_json_shapes_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(json.dumps(payload), encoding="utf-8")
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "0.0.21", "dependencies": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {}}),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match=match):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_requires_non_empty_version(tmp_path: Path) -> None:
    """Reject empty server versions before rendering the runtime manifest."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_empty_version_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({"workspaces": {"catalog": {}}, "overrides": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "", "dependencies": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {}}),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="Expected non-empty string 'version'"):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_main_writes_expected_runtime_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write the rendered package.json with optional fields only when requested."""
    module = _load_module(
        "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_main_test",
    )

    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({
            "workspaces": {"catalog": {"effect": "4.0.0"}},
            "overrides": {"effect": "catalog:"},
        }),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "0.0.21", "dependencies": {"effect": "catalog:"}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {"electron": "40.6.0", "open": "^10.1.0"}}),
        encoding="utf-8",
    )

    output_path = tmp_path / "runtime-package.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_runtime_package_json.py",
            str(source_root),
            "--output",
            str(output_path),
        ],
    )

    module.main()

    rendered = output_path.read_text(encoding="utf-8")
    assert rendered.endswith("\n")
    payload = json.loads(rendered)
    assert payload == {
        "name": "t3code",
        "version": "0.0.21",
        "buildVersion": "0.0.21",
        "private": True,
        "description": "T3 Code desktop runtime",
        "author": "T3 Tools",
        "main": "apps/desktop/dist-electron/main.cjs",
        "dependencies": {"effect": "4.0.0", "open": "^10.1.0"},
        "overrides": {"effect": "4.0.0"},
    }


def test_patch_info_plist_sets_bundle_metadata(tmp_path: Path) -> None:
    """Patching should preserve the plist while updating app-specific keys."""
    module = _load_module(
        "packages/t3code-desktop/patch_info_plist.py",
        "t3code_desktop_plist_test",
    )

    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {"CFBundleName": "Electron", "CFBundleExecutable": "Electron"}, handle
        )

    module.patch_info_plist(
        plist_path,
        app_name="T3 Code (Alpha)",
        bundle_id="com.t3tools.t3code",
        version="0.0.21",
        icon_file="icon.icns",
        url_scheme="t3",
    )

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["CFBundleName"] == "T3 Code (Alpha)"
    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
    assert payload["CFBundleShortVersionString"] == "0.0.21"
    assert payload["CFBundleIconFile"] == "icon.icns"
    assert payload["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["t3"]
    assert payload["CFBundleExecutable"] == "Electron"


def test_patch_info_plist_rejects_non_dictionary_payload(tmp_path: Path) -> None:
    """Reject plist payloads that do not decode to a dictionary."""
    module = _load_module(
        "packages/t3code-desktop/patch_info_plist.py",
        "t3code_desktop_plist_bad_payload_test",
    )

    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(["not", "a", "dict"], handle)

    with pytest.raises(TypeError, match="Expected a plist dictionary"):
        module.patch_info_plist(
            plist_path,
            app_name="T3 Code",
            bundle_id="com.t3tools.t3code",
            version="0.0.21",
            icon_file="icon.icns",
            url_scheme="t3",
        )


def test_patch_info_plist_main_applies_category_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the CLI entrypoint and optional category override."""
    module = _load_module(
        "packages/t3code-desktop/patch_info_plist.py",
        "t3code_desktop_plist_main_test",
    )

    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "Electron"}, handle)

    monkeypatch.setattr(
        "sys.argv",
        [
            "patch_info_plist.py",
            str(plist_path),
            "--app-name",
            "T3 Code",
            "--bundle-id",
            "com.t3tools.t3code",
            "--version",
            "0.0.21",
            "--icon-file",
            "icon.icns",
            "--url-scheme",
            "t3",
            "--category",
            "public.app-category.utilities",
        ],
    )

    module.main()

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["LSApplicationCategoryType"] == "public.app-category.utilities"
    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
    assert payload["CFBundleURLTypes"][0]["CFBundleURLName"] == "com.t3tools.t3code t3"


def test_render_runtime_package_json_main_guard_executes_as_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the script via its __main__ guard."""
    source_root = tmp_path / "t3code"
    (source_root / "apps/server").mkdir(parents=True)
    (source_root / "apps/desktop").mkdir(parents=True)

    (source_root / "package.json").write_text(
        json.dumps({"workspaces": {"catalog": {}}, "overrides": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/server/package.json").write_text(
        json.dumps({"version": "0.0.21", "dependencies": {}}),
        encoding="utf-8",
    )
    (source_root / "apps/desktop/package.json").write_text(
        json.dumps({"dependencies": {}}),
        encoding="utf-8",
    )

    output_path = tmp_path / "runtime-package.json"
    script_path = REPO_ROOT / "packages/t3code-desktop/render_runtime_package_json.py"
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_runtime_package_json.py",
            str(source_root),
            "--output",
            str(output_path),
        ],
    )

    runpy.run_path(str(script_path), run_name="__main__")

    assert json.loads(output_path.read_text(encoding="utf-8"))["version"] == "0.0.21"


def test_patch_info_plist_main_guard_executes_as_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute the plist patcher via its __main__ guard."""
    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "Electron"}, handle)

    script_path = REPO_ROOT / "packages/t3code-desktop/patch_info_plist.py"
    monkeypatch.setattr(
        "sys.argv",
        [
            "patch_info_plist.py",
            str(plist_path),
            "--app-name",
            "T3 Code",
            "--bundle-id",
            "com.t3tools.t3code",
            "--version",
            "0.0.21",
            "--icon-file",
            "icon.icns",
            "--url-scheme",
            "t3",
        ],
    )

    runpy.run_path(str(script_path), run_name="__main__")

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
