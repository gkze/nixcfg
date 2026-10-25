"""Unit tests for T3 Code desktop packaging helpers."""

import json
import plistlib
import runpy
import struct
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


@cache
def _icns_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/make_icns.py",
        "t3code_desktop_icns_test",
    )


@cache
def _bundle_identity_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/verify_bundle_identity.py",
        "t3code_desktop_bundle_identity_test",
    )


@cache
def _fff_patch_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-desktop/patch_fff_asar.py",
        "t3code_desktop_fff_patch_test",
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
    assert payload["devDependencies"] == {
        "electron-builder": "26.8.1",
        "electron-builder-squirrel-windows": "26.8.1",
    }
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


def test_render_runtime_manifest_does_not_promote_pnpm_patch_only_packages(
    tmp_path: Path,
) -> None:
    """A workspace patch does not make its package a runtime dependency."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"packageManager": "pnpm@11.10.0"},
        pnpm_workspace="\n".join((
            "packages: []",
            "catalog:",
            "  '@pierre/diffs': 1.3.0-beta.10",
            "  effect: 4.0.0-beta.103",
            "patchedDependencies:",
            "  '@pierre/diffs@1.3.0-beta.10': patch-hash",
        ))
        + "\n",
        server_package={
            "version": "0.0.38",
            "dependencies": {"effect": "catalog:"},
        },
        desktop_package={"dependencies": {"electron": "43.4.1"}},
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["dependencies"] == {"effect": "4.0.0-beta.103"}


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


def test_render_runtime_package_json_keeps_dev_workspace_references_resolvable(
    tmp_path: Path,
) -> None:
    """Bun installs member devDependencies, so dev workspace refs join the set."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {"packages": ["packages/*"], "catalog": {}},
            "overrides": {},
        },
        server_package={"version": "0.0.25", "dependencies": {}},
        desktop_package={"dependencies": {"@t3tools/ssh": "workspace:*"}},
        package_jsons={
            "packages/ssh": {
                "name": "@t3tools/ssh",
                "devDependencies": {
                    "@t3tools/ssh-testing": "workspace:*",
                    "vitest": "4.0.0",
                },
            },
            "packages/ssh-testing": {
                "name": "@t3tools/ssh-testing",
                "devDependencies": {"@t3tools/fixtures": "workspace:*"},
            },
            "packages/fixtures": {"name": "@t3tools/fixtures"},
            "packages/unused": {"name": "@t3tools/unused"},
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["workspaces"]["packages"] == [
        "packages/fixtures",
        "packages/ssh",
        "packages/ssh-testing",
    ]
    assert payload["dependencies"] == {"@t3tools/ssh": "workspace:*"}


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


def test_render_runtime_package_json_filters_unrelated_runtime_overrides(
    tmp_path: Path,
) -> None:
    """Irrelevant workspace overrides should not affect the runtime lock refresh."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={
            "workspaces": {"catalog": {"effect": "4.0.0"}},
            "overrides": {
                "effect": "catalog:",
                "@clerk/clerk-js": "catalog:",
                "@clerk/clerk-js>@base-org/account": "-",
            },
        },
        server_package={
            "version": "0.0.23",
            "dependencies": {"effect": "catalog:"},
        },
        desktop_package={"dependencies": {"electron": "41.5.0"}},
    )

    payload = module.build_runtime_manifest(
        source_root,
        include_desktop_runtime=False,
    )

    assert payload["dependencies"] == {"effect": "4.0.0"}
    assert payload["overrides"] == {"effect": "4.0.0"}


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
        app_name="T3 Code (Nightly)",
        bundle_id="com.t3tools.t3code",
        version="0.1.3-nightly.20261025.3655",
        icon_file="icon.icns",
        url_schemes=["t3code", "t3code-dev"],
    )

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["CFBundleName"] == "T3 Code (Nightly)"
    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
    assert payload["CFBundleShortVersionString"] == "0.1.3-nightly.20261025.3655"
    assert payload["CFBundleIconFile"] == "icon.icns"
    assert payload["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == [
        "t3code",
        "t3code-dev",
    ]
    assert payload["CFBundleExecutable"] == "Electron"


def test_patch_info_plist_keeps_unrelated_url_types(tmp_path: Path) -> None:
    """Electron's own http/https registration stays; stale copies are replaced."""
    module = _plist_module()

    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleExecutable": "Electron",
                "CFBundleURLTypes": [
                    {
                        "CFBundleTypeRole": "Viewer",
                        "CFBundleURLName": "Web site URL",
                        "CFBundleURLSchemes": ["http", "https"],
                    },
                    {
                        "CFBundleTypeRole": "Editor",
                        "CFBundleURLName": "stale",
                        "CFBundleURLSchemes": ["t3code"],
                    },
                ],
            },
            handle,
        )

    module.patch_info_plist(
        plist_path,
        app_name="T3 Code (Nightly)",
        bundle_id="com.t3tools.t3code",
        version="0.1.3-nightly.20261025.3655",
        icon_file="icon.icns",
        url_schemes=["t3code", "t3code-dev"],
    )

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["CFBundleURLTypes"] == [
        {
            "CFBundleTypeRole": "Editor",
            "CFBundleURLName": "com.t3tools.t3code t3code",
            "CFBundleURLSchemes": ["t3code", "t3code-dev"],
        },
        {
            "CFBundleTypeRole": "Viewer",
            "CFBundleURLName": "Web site URL",
            "CFBundleURLSchemes": ["http", "https"],
        },
    ]


def test_patch_info_plist_requires_a_url_scheme(tmp_path: Path) -> None:
    """A bundle without a scheme cannot receive deep links or OAuth handoffs."""
    module = _plist_module()

    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "Electron"}, handle)

    with pytest.raises(ValueError, match="at least one URL scheme"):
        module.patch_info_plist(
            plist_path,
            app_name="T3 Code",
            bundle_id="com.t3tools.t3code",
            version="0.1.3",
            icon_file="icon.icns",
            url_schemes=[],
        )


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
            url_schemes=["t3code"],
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
            "t3code",
            "--url-scheme",
            "t3code-dev",
            "--category",
            "public.app-category.utilities",
        ],
    )

    module.main()

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["LSApplicationCategoryType"] == "public.app-category.utilities"
    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
    assert payload["CFBundleURLTypes"][0]["CFBundleURLName"] == (
        "com.t3tools.t3code t3code"
    )


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
            "t3code",
        ],
    )

    runpy.run_path(str(script_path), run_name="__main__")

    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)

    assert payload["CFBundleIdentifier"] == "com.t3tools.t3code"
    assert payload["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["t3code"]


def test_render_runtime_package_json_resolves_catalog_for_nested_overrides(
    tmp_path: Path,
) -> None:
    """A bare ``catalog:`` on ``parent>child`` resolves the child's catalog entry."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"packageManager": "pnpm@11.27.0"},
        pnpm_workspace="\n".join((
            "packages:",
            '  - "packages/*"',
            "catalog:",
            "  effect: 4.0.5",
            "  '@electron-webauthn/macos': 1.2.3",
            "overrides:",
            "  effect: 'catalog:'",
            "  '@opencode/protocol>effect': 'catalog:'",
            "  '@opencode/schema>effect@4': 'catalog:'",
            "  'electron-webauthn>@electron-webauthn/macos': 'catalog:'",
            "  'unrelated>effect': 'catalog:'",
        ))
        + "\n",
        server_package={
            "version": "0.0.25",
            "dependencies": {
                "effect": "catalog:",
                "@opencode/protocol": "2.0.0",
                "@opencode/schema": "2.0.0",
                "electron-webauthn": "1.0.0",
            },
        },
    )

    payload = module.build_runtime_manifest(source_root, include_desktop_runtime=False)

    assert payload["overrides"] == {
        "effect": "4.0.5",
        "@opencode/protocol>effect": "4.0.5",
        "@opencode/schema>effect@4": "4.0.5",
        "electron-webauthn>@electron-webauthn/macos": "1.2.3",
    }


def test_render_runtime_package_json_tracks_the_desktop_entry_point(
    tmp_path: Path,
) -> None:
    """Upstream renamed the packaged entry from main.cjs to boot.cjs."""
    module = _runtime_manifest_module()

    source_root = _runtime_source(
        tmp_path,
        root_package={"workspaces": {"catalog": {}}, "overrides": {}},
        desktop_package={
            "main": "dist-electron/boot.cjs",
            "dependencies": {"electron": "44.9.3"},
        },
    )

    payload = module.build_runtime_manifest(source_root)

    assert payload["main"] == "apps/desktop/dist-electron/boot.cjs"


def _png_stub(size: int) -> bytes:
    """Return the smallest payload ``make_icns`` considers a square PNG."""
    header = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", size, size)
    )
    return header + b"\x08\x06\x00\x00\x00" + b"\x00" * 12


def _write_iconset(root: Path, module: ModuleType) -> Path:
    iconset = root / "icon.iconset"
    iconset.mkdir()
    for name, (size, _chunk_type) in module.ICONSET_ENTRIES.items():
        (iconset / name).write_bytes(_png_stub(size))
    return iconset


def test_make_icns_assembles_every_channel_size(tmp_path: Path) -> None:
    """The container must carry one chunk per iconset size, in file order."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)

    payload = module.build_icns(module.collect_chunks(iconset))

    assert module.read_icns_chunk_types(payload) == module.expected_chunk_types()
    module.verify_icns(payload)


def test_make_icns_rejects_a_stock_electron_icon(tmp_path: Path) -> None:
    """Shipping Electron's own artwork unchanged is the regression to catch."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)
    payload = module.build_icns(module.collect_chunks(iconset))

    with pytest.raises(module.IconsError, match="stock icon"):
        module.verify_icns(payload, reference=payload)


def test_make_icns_rejects_incomplete_or_misnamed_iconset(tmp_path: Path) -> None:
    """Every upstream size must exist at the pixel size its name promises."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)

    (iconset / "icon_512x512@2x.png").write_bytes(_png_stub(512))
    with pytest.raises(module.IconsError, match="expected 1024x1024"):
        module.collect_chunks(iconset)

    (iconset / "icon_512x512@2x.png").unlink()
    with pytest.raises(module.IconsError, match="missing"):
        module.collect_chunks(iconset)


def test_make_icns_rejects_malformed_png_and_chunk_types(tmp_path: Path) -> None:
    """Reject non-PNG inputs and containers that cannot be read back."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)
    (iconset / "icon_16x16.png").write_bytes(b"not a png")
    with pytest.raises(module.IconsError, match="not a PNG file"):
        module.collect_chunks(iconset)

    with pytest.raises(module.IconsError, match="four characters"):
        module.build_icns([("toolong", _png_stub(16))])
    with pytest.raises(module.IconsError, match="at least one chunk"):
        module.build_icns([])
    with pytest.raises(module.IconsError, match="not an ICNS container"):
        module.read_icns_chunk_types(b"nope")


def test_make_icns_reports_unsupported_png_headers(tmp_path: Path) -> None:
    """Reject artwork that is not a square PNG before assembling a container."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)

    bad_header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"XXXX" + b"\x00" * 21
    (iconset / "icon_16x16.png").write_bytes(bad_header)
    with pytest.raises(module.IconsError, match="missing its leading IHDR chunk"):
        module.collect_chunks(iconset)

    wide = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", 32, 16)
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 12
    )
    (iconset / "icon_16x16.png").write_bytes(wide)
    with pytest.raises(module.IconsError, match="is not square"):
        module.collect_chunks(iconset)


def test_make_icns_verifies_the_expected_chunk_set(tmp_path: Path) -> None:
    """A container missing a channel size must fail verification."""
    module = _icns_module()
    iconset = _write_iconset(tmp_path, module)
    chunks = module.collect_chunks(iconset)

    with pytest.raises(module.IconsError, match="ICNS chunks are"):
        module.verify_icns(module.build_icns(chunks[:-1]))


def test_make_icns_rejects_a_container_with_a_wrong_length(tmp_path: Path) -> None:
    """A container whose header length disagrees with its bytes is unreadable."""
    module = _icns_module()
    payload = bytearray(module.build_icns([("icp4", _png_stub(16))]))
    payload[4:8] = struct.pack(">I", len(payload) + 4)

    with pytest.raises(module.IconsError, match="does not match"):
        module.read_icns_chunk_types(bytes(payload))


def test_make_icns_renders_through_the_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build step renders with a scratch iconset and verifies the result."""
    module = _icns_module()
    source = tmp_path / "artwork.png"
    source.write_bytes(_png_stub(1024))
    output = tmp_path / "icon.icns"

    def _fake_scale(png: Path, iconset: Path, **_kwargs: object) -> None:
        _ = png
        iconset.mkdir(parents=True, exist_ok=True)
        for name, (size, _chunk_type) in module.ICONSET_ENTRIES.items():
            (iconset / name).write_bytes(_png_stub(size))

    monkeypatch.setattr(module, "scale_iconset", _fake_scale)

    monkeypatch.setattr(
        "sys.argv", ["make_icns.py", "render", str(source), str(output)]
    )
    module.main()
    module.verify_icns(output.read_bytes())

    staged = tmp_path / "staged-iconset"
    monkeypatch.setattr(
        "sys.argv",
        [
            "make_icns.py",
            "render",
            str(source),
            str(output),
            "--iconset",
            str(staged),
        ],
    )
    module.main()
    assert (staged / "icon_16x16.png").is_file()

    monkeypatch.setattr("sys.argv", ["make_icns.py", "verify", str(output)])
    runpy.run_path(
        str(REPO_ROOT / "packages/t3code-desktop/make_icns.py"),
        run_name="__main__",
    )


def test_make_icns_scales_artwork_with_imagemagick(tmp_path: Path) -> None:
    """Scaling drives ImageMagick once per size, writing 8-bit PNG32 output."""
    module = _icns_module()
    calls: list[list[str]] = []

    module.scale_iconset(
        tmp_path / "nightly-macos-1024.png",
        tmp_path / "icon.iconset",
        run=lambda command, check: calls.append(command),
    )

    assert len(calls) == len(module.ICONSET_ENTRIES)
    first = calls[0]
    assert first[0] == module.MAGICK
    assert first[1].endswith("nightly-macos-1024.png")
    resize_index = first.index("-resize")
    assert first[resize_index + 1] == "16x16"
    assert first[-1].endswith("PNG32:") or "PNG32:" in first[-1]
    assert (tmp_path / "icon.iconset").is_dir()


def test_verify_bundle_identity_checks_channel_metadata(tmp_path: Path) -> None:
    """Bundle identity covers name, version, icon, and every URL scheme."""
    plist_module = _plist_module()
    identity = _bundle_identity_module()
    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        # electron-builder names the bundle executable after the product name and
        # ``patch_info_plist`` deliberately leaves that key alone, so the input
        # has to carry the executable the packaging check will verify.
        plistlib.dump({"CFBundleExecutable": "T3 Code (Nightly)"}, handle)
    plist_module.patch_info_plist(
        plist_path,
        app_name="T3 Code (Nightly)",
        bundle_id="com.t3tools.t3code",
        version="0.1.3-nightly.20261025.3655",
        icon_file="icon.icns",
        url_schemes=["t3code", "t3code-dev"],
    )

    identity.verify_bundle_identity(
        plist_path,
        app_name="T3 Code (Nightly)",
        bundle_id="com.t3tools.t3code",
        version="0.1.3-nightly.20261025.3655",
        icon_file="icon.icns",
        url_schemes=["t3code"],
    )

    with pytest.raises(identity.BundleIdentityError, match="mismatched|drifted"):
        identity.verify_bundle_identity(
            plist_path,
            app_name="T3 Code (Nightly)",
            bundle_id="com.t3tools.t3code",
            version="0.1.2",
            icon_file="icon.icns",
            url_schemes=["t3code"],
        )
    with pytest.raises(
        identity.BundleIdentityError, match="does not declare URL schemes"
    ):
        identity.verify_bundle_identity(
            plist_path,
            app_name="T3 Code (Nightly)",
            bundle_id="com.t3tools.t3code",
            version="0.1.3-nightly.20261025.3655",
            icon_file="icon.icns",
            url_schemes=["t3code", "t3code-prod"],
        )


def _identity_plist(**overrides: object) -> dict[str, object]:
    """Return a plist carrying the metadata a packaged bundle must declare."""
    payload: dict[str, object] = {
        "CFBundleName": "T3 Code (Nightly)",
        "CFBundleDisplayName": "T3 Code (Nightly)",
        "CFBundleExecutable": "T3 Code (Nightly)",
        "CFBundleIdentifier": "com.t3tools.t3code",
        "CFBundleShortVersionString": "0.1.3-nightly.20261025.3655",
        "CFBundleVersion": "0.1.3-nightly.20261025.3655",
        "CFBundleIconFile": "icon.icns",
        "CFBundleURLTypes": [{"CFBundleURLSchemes": ["t3code", "t3code-dev"]}],
    }
    payload.update(overrides)
    return payload


def test_verify_bundle_identity_rejects_malformed_metadata(tmp_path: Path) -> None:
    """Malformed plists and URL declarations fail closed instead of crashing."""
    identity = _bundle_identity_module()
    expected = {
        "app_name": "T3 Code (Nightly)",
        "bundle_id": "com.t3tools.t3code",
        "version": "0.1.3-nightly.20261025.3655",
        "icon_file": "icon.icns",
        "url_schemes": ["t3code"],
    }

    listing = tmp_path / "listing.plist"
    with listing.open("wb") as handle:
        plistlib.dump(["not", "a", "dictionary"], handle)
    with pytest.raises(identity.BundleIdentityError, match="Expected a plist"):
        identity.verify_bundle_identity(listing, **expected)

    for index, url_types in enumerate((
        "t3code",
        ["not-a-dict", {"CFBundleURLSchemes": "t3code"}],
    )):
        path = tmp_path / f"url-types-{index}.plist"
        with path.open("wb") as handle:
            plistlib.dump(_identity_plist(CFBundleURLTypes=url_types), handle)
        with pytest.raises(
            identity.BundleIdentityError,
            match="does not declare URL schemes",
        ):
            identity.verify_bundle_identity(path, **expected)


def test_verify_bundle_identity_reports_through_the_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The packaging check surfaces a drift as a non-zero exit."""
    plist_module = _plist_module()
    plist_path = tmp_path / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "T3 Code (Nightly)"}, handle)
    plist_module.patch_info_plist(
        plist_path,
        app_name="T3 Code (Nightly)",
        bundle_id="com.t3tools.t3code",
        version="0.1.3-nightly.20261025.3655",
        icon_file="icon.icns",
        url_schemes=["t3code"],
    )
    script_path = REPO_ROOT / "packages/t3code-desktop/verify_bundle_identity.py"
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_bundle_identity.py",
            str(plist_path),
            "--app-name",
            "T3 Code (Nightly)",
            "--bundle-id",
            "com.t3tools.t3code",
            "--version",
            "0.1.3",
            "--icon-file",
            "icon.icns",
            "--url-scheme",
            "t3code",
        ],
    )

    # The only drift is the requested ``--version``, so the surfaced error must
    # name that key instead of any unrelated mismatch.
    with pytest.raises(
        SystemExit,
        match=r"packaged bundle identity drifted: CFBundleShortVersionString",
    ):
        runpy.run_path(str(script_path), run_name="__main__")


def test_patch_fff_asar_redirects_the_dylib_lookup() -> None:
    """The packaged lookup must resolve beside app.asar, not inside it."""
    module = _fff_patch_module()
    source = (
        "function resolveFromNpmPackage() {\n"
        "    if (existsSync(binaryPath)) {\n"
        "      return binaryPath;\n"
        "    }\n"
        "}\n"
    )

    patched = module.patch_source(source)

    assert ".asar.unpacked/" in patched
    assert "return binaryPath;\n" not in patched
    assert patched.count("return binaryPath.replace(") == 1


def test_patch_fff_asar_runs_as_a_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The derivation invokes the helper as a script, not as an import."""
    target = tmp_path / "index.cjs"
    target.write_text(
        "function resolveFromNpmPackage() {\n  return binaryPath;\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["patch_fff_asar.py", str(target)],
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(
            str(REPO_ROOT / "packages/t3code-desktop/patch_fff_asar.py"),
            run_name="__main__",
        )

    assert exc.value.code == 0
    assert ".asar.unpacked/" in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("occurrences", [0, 2])
def test_patch_fff_asar_rejects_unexpected_layouts(occurrences: int) -> None:
    """A moved or duplicated lookup must fail instead of patching blindly."""
    module = _fff_patch_module()
    source = "      return binaryPath;\n" * occurrences

    with pytest.raises(module.FffPatchError, match="expected exactly one"):
        module.patch_source(source)


def test_patch_fff_asar_cli_writes_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build step drives the rewrite through the CLI entry point."""
    module = _fff_patch_module()
    target = tmp_path / "index.js"
    target.write_text(
        "\n".join((
            'import { existsSync } from "fs";',
            "function resolveFromNpmPackage() {",
            "    if (existsSync(binaryPath)) {",
            "      return binaryPath;",
            "    }",
            "}",
        ))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["patch_fff_asar.py", str(target)])

    assert module.main() == 0
    assert ".asar.unpacked/" in target.read_text(encoding="utf-8")

    broken = tmp_path / "broken.js"
    broken.write_text("nothing to patch\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["patch_fff_asar.py", str(broken)])
    assert module.main() == 1
    assert broken.read_text(encoding="utf-8") == "nothing to patch\n"

    # Upstream may stop shipping one of the bundles; the remaining entry point
    # still has to be patched.
    second = tmp_path / "index.cjs"
    second.write_text(
        "function resolveFromNpmPackage() {\n  return binaryPath;\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["patch_fff_asar.py", str(tmp_path / "missing.cjs"), str(second)],
    )
    assert module.main() == 0
    assert ".asar.unpacked/" in second.read_text(encoding="utf-8")

    # Nothing patchable at all is a build error, not a silent pass.
    monkeypatch.setattr(
        "sys.argv",
        ["patch_fff_asar.py", str(tmp_path / "missing.cjs")],
    )
    assert module.main() == 1
