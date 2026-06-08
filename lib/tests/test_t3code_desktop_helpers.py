"""Unit tests for T3 Code desktop packaging helpers."""

from __future__ import annotations

import json
import plistlib
import runpy
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


@cache
def _runtime_manifest_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/render_runtime_package_json.py",
        "t3code_desktop_manifest_test",
    )


@cache
def _plist_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/patch_info_plist.py",
        "t3code_desktop_plist_test",
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _runtime_source(
    tmp_path: Path,
    *,
    root_package: object,
    server_package: object | None = None,
    desktop_package: object | None = None,
    package_jsons: dict[str, object] | None = None,
    pnpm_workspace: str | None = None,
) -> Path:
    source_root = tmp_path / "t3code"
    _write_json(source_root / "package.json", root_package)
    if pnpm_workspace is not None:
        (source_root / "pnpm-workspace.yaml").write_text(
            pnpm_workspace, encoding="utf-8"
        )
    _write_json(
        source_root / "apps/server/package.json",
        {"version": "0.0.21", "dependencies": {}}
        if server_package is None
        else server_package,
    )
    _write_json(
        source_root / "apps/desktop/package.json",
        {"dependencies": {}} if desktop_package is None else desktop_package,
    )
    for rel_dir, payload in (package_jsons or {}).items():
        _write_json(source_root / rel_dir / "package.json", payload)
    return source_root


def test_render_runtime_package_json_resolves_catalog_dependencies(
    tmp_path: Path,
) -> None:
    """The staged runtime manifest should resolve catalog specs and omit Electron."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {
                "catalog": {
                    "effect": "4.0.0-beta.45",
                    "@effect/platform-node": "4.0.0-beta.45",
                }
            },
            "overrides": {
                "effect": "catalog:",
            },
        },
        server_package={
            "version": "0.0.21",
            "dependencies": {
                "effect": "catalog:",
                "open": "^10.1.0",
            },
        },
        desktop_package={
            "dependencies": {
                "@effect/platform-node": "catalog:",
                "electron": "40.6.0",
                "electron-updater": "^6.6.2",
            },
        },
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


def test_render_runtime_package_json_reads_pnpm_workspace_metadata(
    tmp_path: Path,
) -> None:
    """Current upstream T3 Code stores workspace metadata in pnpm YAML."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"packageManager": "pnpm@10.24.0"},
        pnpm_workspace="\n".join((
            "packages:",
            '  - "packages/*"',
            "catalog:",
            "  effect: 4.0.0-beta.73",
            "  vite: npm:@voidzero-dev/vite-plus-core@latest",
            "overrides:",
            "  effect: 'catalog:'",
        ))
        + "\n",
        server_package={
            "version": "0.0.24",
            "dependencies": {
                "effect": "catalog:",
                "@t3tools/shared": "workspace:*",
            },
        },
        desktop_package={"dependencies": {"electron": "41.5.0", "vite": "catalog:"}},
        package_jsons={
            "packages/shared": {"name": "@t3tools/shared", "dependencies": {}},
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {
        "effect": "4.0.0-beta.73",
        "@t3tools/shared": "workspace:*",
        "vite": "npm:@voidzero-dev/vite-plus-core@latest",
    }
    assert payload["overrides"] == {"effect": "4.0.0-beta.73"}
    assert payload["workspaces"] == {
        "packages": ["packages/shared"],
        "catalog": {
            "effect": "4.0.0-beta.73",
            "vite": "npm:@voidzero-dev/vite-plus-core@latest",
        },
    }


def test_render_runtime_package_json_accepts_package_json_workspace_arrays(
    tmp_path: Path,
) -> None:
    """Some upstream package-manager layouts keep workspaces and catalog in package.json."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": ["packages/*"],
            "catalog": {"effect": "4.0.0-beta.80", "yaml": "^2.9.0"},
            "overrides": {"effect": "catalog:"},
        },
        server_package={
            "version": "0.0.24",
            "dependencies": {
                "effect": "catalog:",
                "@t3tools/shared": "workspace:*",
            },
        },
        desktop_package={"dependencies": {"electron": "41.5.0"}},
        package_jsons={
            "packages/shared": {
                "name": "@t3tools/shared",
                "dependencies": {"yaml": "catalog:"},
            },
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {
        "effect": "4.0.0-beta.80",
        "@t3tools/shared": "workspace:*",
        "yaml": "^2.9.0",
    }
    assert payload["overrides"] == {"effect": "4.0.0-beta.80"}
    assert payload["workspaces"] == {
        "packages": ["packages/shared"],
        "catalog": {"effect": "4.0.0-beta.80", "yaml": "^2.9.0"},
    }


def test_render_runtime_package_json_accepts_absent_workspaces(
    tmp_path: Path,
) -> None:
    """Root package.json workspaces can be absent when only root catalog data is needed."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "catalog": {"effect": "4.0.0-beta.80"},
            "overrides": {"effect": "catalog:"},
        },
        server_package={
            "version": "0.0.24",
            "dependencies": {"effect": "catalog:"},
        },
        desktop_package={"dependencies": {"electron": "41.5.0"}},
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {"effect": "4.0.0-beta.80"}
    assert payload["overrides"] == {"effect": "4.0.0-beta.80"}
    assert "workspaces" not in payload


def test_render_runtime_package_json_merges_root_catalog_into_object_workspaces(
    tmp_path: Path,
) -> None:
    """Package-manager catalog data can live beside object-form workspaces."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {
                "packages": ["packages/*"],
                "catalog": {
                    "effect": "4.0.0-beta.79",
                    "yaml": "^2.9.0",
                },
            },
            "catalog": {
                "effect": "4.0.0-beta.80",
                "jose": "6.2.2",
            },
            "overrides": {"effect": "catalog:", "jose": "catalog:"},
        },
        server_package={
            "version": "0.0.24",
            "dependencies": {
                "effect": "catalog:",
                "@t3tools/shared": "workspace:*",
            },
        },
        desktop_package={"dependencies": {"electron": "41.5.0"}},
        package_jsons={
            "packages/shared": {
                "name": "@t3tools/shared",
                "dependencies": {"jose": "catalog:", "yaml": "catalog:"},
            },
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {
        "effect": "4.0.0-beta.80",
        "@t3tools/shared": "workspace:*",
        "jose": "6.2.2",
        "yaml": "^2.9.0",
    }
    assert payload["overrides"] == {
        "effect": "4.0.0-beta.80",
        "jose": "6.2.2",
    }
    assert payload["workspaces"] == {
        "packages": ["packages/shared"],
        "catalog": {
            "effect": "4.0.0-beta.80",
            "jose": "6.2.2",
            "yaml": "^2.9.0",
        },
    }


def test_render_runtime_package_json_preserves_runtime_workspace_dependencies(
    tmp_path: Path,
) -> None:
    """Runtime workspace deps need an explicit, minimal workspaces stanza."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {
                "packages": ["packages/*"],
                "catalog": {"effect": "4.0.0"},
            },
            "overrides": {},
        },
        server_package={"version": "0.0.23", "dependencies": {"effect": "catalog:"}},
        desktop_package={
            "dependencies": {
                "@t3tools/ssh": "workspace:*",
                "@t3tools/tailscale": "workspace:*",
                "electron": "41.5.0",
            }
        },
        package_jsons={
            "packages/contracts": {
                "name": "@t3tools/contracts",
                "dependencies": {"effect": "catalog:"},
            },
            "packages/shared": {
                "name": "@t3tools/shared",
                "dependencies": {"@t3tools/contracts": "workspace:*"},
            },
            "packages/ssh": {
                "name": "@t3tools/ssh",
                "dependencies": {"@t3tools/shared": "workspace:*"},
                "optionalDependencies": {"@t3tools/shared": "workspace:*"},
            },
            "packages/tailscale": {
                "name": "@t3tools/tailscale",
                "dependencies": {"effect": "catalog:"},
            },
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {
        "effect": "4.0.0",
        "@t3tools/ssh": "workspace:*",
        "@t3tools/tailscale": "workspace:*",
    }
    assert payload["workspaces"] == {
        "packages": [
            "packages/contracts",
            "packages/shared",
            "packages/ssh",
            "packages/tailscale",
        ],
        "catalog": {"effect": "4.0.0"},
    }
    assert "electron" not in payload["dependencies"]


def test_render_runtime_package_json_promotes_workspace_runtime_dependencies(
    tmp_path: Path,
) -> None:
    """Electron packaging needs workspace package runtime deps at the root."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {
                "packages": ["packages/*"],
                "catalog": {
                    "@noble/curves": "1.9.1",
                    "@noble/hashes": "1.8.0",
                    "effect": "4.0.0-beta.78",
                },
            },
            "overrides": {},
        },
        server_package={"version": "0.0.24", "dependencies": {}},
        desktop_package={
            "dependencies": {
                "@t3tools/shared": "workspace:*",
                "electron": "41.5.0",
            }
        },
        package_jsons={
            "packages/shared": {
                "name": "@t3tools/shared",
                "dependencies": {
                    "@noble/curves": "catalog:",
                    "effect": "catalog:",
                },
                "optionalDependencies": {
                    "@noble/hashes": "catalog:",
                },
            },
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {
        "@t3tools/shared": "workspace:*",
        "@noble/curves": "1.9.1",
        "@noble/hashes": "1.8.0",
        "effect": "4.0.0-beta.78",
    }
    assert payload["workspaces"] == {
        "packages": ["packages/shared"],
        "catalog": {
            "@noble/curves": "1.9.1",
            "@noble/hashes": "1.8.0",
            "effect": "4.0.0-beta.78",
        },
    }


def test_render_runtime_package_json_can_omit_desktop_runtime_dependencies(
    tmp_path: Path,
) -> None:
    """The standalone CLI runtime should not pull desktop-only dependencies."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
        server_package={"version": "0.0.23", "dependencies": {"node-pty": "^1.1.0"}},
        desktop_package={
            "dependencies": {
                "@t3tools/desktop-only": "workspace:*",
                "electron": "41.5.0",
            }
        },
    )

    payload = module.build_runtime_manifest(
        source_root,
        include_desktop_runtime=False,
    )

    assert payload["dependencies"] == {"node-pty": "^1.1.0"}
    assert "workspaces" not in payload


def test_render_runtime_package_json_rejects_unresolved_workspace_dependency(
    tmp_path: Path,
) -> None:
    """Raise a targeted error when workspace deps are absent from root workspaces."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"packages": [], "catalog": {}}, "overrides": {}},
        server_package={"version": "0.0.23", "dependencies": {}},
        desktop_package={"dependencies": {"@t3tools/missing": "workspace:*"}},
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to resolve workspace dependency '@t3tools/missing'",
    ):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_rejects_unresolved_catalog_dependency(
    tmp_path: Path,
) -> None:
    """Raise a targeted error when a catalog dependency cannot be resolved."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
        server_package={"version": "0.0.21", "dependencies": {"effect": "catalog:"}},
    )

    with pytest.raises(
        RuntimeError, match="expected key 'effect' in the workspace catalog"
    ):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_rejects_invalid_pnpm_workspace_yaml(
    tmp_path: Path,
) -> None:
    """Reject pnpm workspace metadata unless the YAML root is a mapping."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
        pnpm_workspace="[]\n",
    )

    with pytest.raises(TypeError, match="Expected a YAML object"):
        module.build_runtime_manifest(source_root)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not", "an", "object"], "Expected a JSON object"),
        (
            {"workspaces": "packages/*"},
            "Expected 'workspaces' to be a JSON object or string list",
        ),
        ({"workspaces": {"catalog": []}}, "Expected 'catalog' to be a JSON object"),
        (
            {"workspaces": {"catalog": {}}, "overrides": {"effect": 1}},
            "Expected string entries in 'overrides'",
        ),
        (
            {"workspaces": {"catalog": {}, "packages": [1]}, "overrides": {}},
            "Expected 'packages' to be a string list",
        ),
        (
            {"workspaces": ["packages/*", 1], "catalog": {}, "overrides": {}},
            "Expected 'workspaces' to be a string list",
        ),
    ],
)
def test_render_runtime_package_json_rejects_invalid_json_shapes(
    tmp_path: Path,
    payload: object,
    match: str,
) -> None:
    """Validate helper branches that reject malformed JSON payloads and maps."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package=payload,
    )

    with pytest.raises(TypeError, match=match):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_requires_non_empty_version(tmp_path: Path) -> None:
    """Reject empty server versions before rendering the runtime manifest."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
        server_package={"version": "", "dependencies": {}},
    )

    with pytest.raises(TypeError, match="Expected non-empty string 'version'"):
        module.build_runtime_manifest(source_root)


def test_render_runtime_package_json_main_writes_expected_runtime_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write the rendered package.json with optional fields only when requested."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {"catalog": {"effect": "4.0.0"}},
            "overrides": {"effect": "catalog:"},
        },
        server_package={"version": "0.0.21", "dependencies": {"effect": "catalog:"}},
        desktop_package={"dependencies": {"electron": "40.6.0", "open": "^10.1.0"}},
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
    module = _plist_module()

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
    module = _plist_module()

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
    module = _plist_module()

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
    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
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
