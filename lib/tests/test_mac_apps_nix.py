"""Regression checks for macOS application bundle management."""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from functools import cache
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _module_output(relative_path: str) -> AttributeSet:
    """Parse one Nix module and return its top-level output attrset."""
    expr = expect_instance(
        parse_nix_expr(Path(REPO_ROOT / relative_path).read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _mac_apps_script(mode: str, *, writable: bool) -> str:
    """Evaluate ``systemApplicationsScript`` for a fake macOS app entry."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake "nixpkgs";
          pkgs = import nixpkgs {{ system = "aarch64-darwin"; }};
          macApps = import {root}/lib/mac-apps.nix {{ inherit (pkgs) lib; inherit pkgs; }};
          fakePkg = {{
            pname = "fake-app";
            outPath = "/nix/store/fake-app";
            passthru.macApp = {{
              bundleName = "Fake.app";
              bundleRelPath = "Applications/Fake.app";
              installMode = "{mode}";
            }};
          }};
        in
          macApps.systemApplicationsScript {{
            entries = [ {{ package = fakePkg; bundleName = "Fake.app"; mode = "{mode}"; }} ];
            stateDirectory = "/Applications/.nixcfg-mac-apps";
            stateName = "test-manager";
            writable = {"true" if writable else "false"};
          }}
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@cache
def _profile_bundle_leak_audit_script(
    managed_bundle_names: tuple[str, ...],
    package_paths: tuple[str, ...],
) -> str:
    """Evaluate ``profileBundleLeakAuditScript`` for fake Home Manager packages."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake "nixpkgs";
          pkgs = import nixpkgs {{ system = "aarch64-darwin"; }};
          macApps = import {root}/lib/mac-apps.nix {{ inherit (pkgs) lib; inherit pkgs; }};
        in
          macApps.profileBundleLeakAuditScript {{
            managedBundleNames = {json.dumps(list(managed_bundle_names))};
            packagePaths = {json.dumps(list(package_paths))};
            label = "home.packages";
          }}
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_copy_mode_replaces_symlinked_application_destinations() -> None:
    """Copy-mode installs should replace old symlink targets before rsync."""
    script = _mac_apps_script("copy", writable=True)

    assert 'if [ -L "$dst" ] || { [ -e "$dst" ] && [ ! -d "$dst" ]; }; then' in script
    assert 'rm -rf -- "${dst:?}"' in script


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_manifest_cleanup_checks_other_mac_app_managers_first() -> None:
    """Stale cleanup should not delete apps still claimed by another manifest."""
    script = _mac_apps_script("symlink", writable=False)

    assert "app_in_other_manifests() {" in script
    assert "stateFile=/Applications/.nixcfg-mac-apps/test-manager.txt" in script
    assert (
        'echo "keeping $targetDirectory/$managedApp because another manifest still '
        'manages it..." >&2' in script
    )


def test_embedded_home_manager_defers_system_app_management_to_darwin() -> None:
    """Integrated nix-darwin should keep one /Applications owner and replay cleanup."""
    darwin_config = expect_instance(
        expect_binding(
            _module_output("modules/darwin/base.nix").values, "config"
        ).value,
        AttributeSet,
    )
    darwin_system = expect_instance(
        expect_binding(darwin_config.values, "system").value,
        AttributeSet,
    )
    darwin_activation_scripts = expect_instance(
        expect_binding(darwin_system.values, "activationScripts").value,
        AttributeSet,
    )
    darwin_applications = expect_instance(
        expect_binding(darwin_activation_scripts.values, "applications").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(darwin_applications.values, "text").value,
        """
        lib.mkAfter (
          macApps.systemApplicationsScript {
            entries = activeMacAppEntries;
            stateDirectory = "/Applications/.nixcfg-mac-apps";
            stateName = "darwin-system";
            writable = false;
          }
        )
        """,
    )

    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value,
        AttributeSet,
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home_activation.values, "nixcfgSystemApplications").value,
        """
        lib.mkIf standaloneActivation (
          lib.hm.dag.entryAfter [ "installPackages" ] (
            macApps.systemApplicationsScript {
              entries = cfg.systemApplications;
              stateDirectory = "/Applications/.nixcfg-mac-apps";
              stateName = "home-manager";
              writable = true;
            }
          )
        )
        """,
    )


def test_home_manager_mac_app_module_asserts_managed_apps_stay_out_of_home_packages() -> (
    None
):
    """Managed macOS app bundles should not also be installed via ``home.packages``."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    assertions = expect_instance(
        expect_binding(home_config.values, "assertions").value,
        FunctionCall,
    )
    optionals_call = expect_instance(assertions.name, FunctionCall)
    assert_nix_ast_equal(optionals_call.name, "lib.optionals")
    assert_nix_ast_equal(optionals_call.argument, "cfg.systemApplications != [ ]")

    assertion_list = expect_instance(assertions.argument, NixList).value
    assert len(assertion_list) == 2

    unique_call = expect_instance(
        expect_instance(assertion_list[0], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(unique_call.name, "macApps.uniqueBundleNamesAssertion")
    assert_nix_ast_equal(unique_call.argument, "cfg.systemApplications")

    overlap_call = expect_instance(
        expect_instance(assertion_list[1], Parenthesis).value,
        FunctionCall,
    )
    assert_nix_ast_equal(
        overlap_call.name, "macApps.managedAppsNotInPackageListsAssertion"
    )
    overlap_args = expect_instance(overlap_call.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(overlap_args.values, "entries").value,
        "cfg.systemApplications",
    )

    package_lists = expect_instance(
        expect_binding(overlap_args.values, "packageLists").value,
        NixList,
    )
    assert len(package_lists.value) == 1
    package_list = expect_instance(package_lists.value[0], AttributeSet)
    label = expect_instance(
        expect_binding(package_list.values, "label").value,
        StringPrimitive,
    )
    assert label.value == "home.packages"
    assert_nix_ast_equal(
        package_list,
        """
        {
          label = "home.packages";
          inherit (config.home) packages;
        }
        """,
    )


def test_home_manager_mac_app_module_audits_profile_bundle_leaks() -> None:
    """Home Manager should audit profile package outputs for managed app bundles."""
    home_config = expect_instance(
        expect_binding(
            _module_output("modules/home/darwin.nix").values, "config"
        ).value,
        AttributeSet,
    )
    home_binding = expect_instance(
        expect_binding(home_config.values, "home").value, AttributeSet
    )
    home_activation = expect_instance(
        expect_binding(home_binding.values, "activation").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(home_activation.values, "nixcfgProfileAppBundleAudit").value,
        """
        lib.mkIf (cfg.systemApplications != [ ]) (
          lib.hm.dag.entryAfter [ "installPackages" ] (
            macApps.profileBundleLeakAuditScript {
              packagePaths = map toString config.home.packages;
              managedBundleNames = map (entry: entry.bundleName) cfg.systemApplications;
              label = "home.packages";
            }
          )
        )
        """,
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_bundle_leak_audit_script_reports_managed_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Activation audit should fail if managed bundles leak through package outputs."""
    managed_package = tmp_path / "cursor-package"
    (managed_package / "Applications" / "Cursor.app").mkdir(parents=True)

    script = _profile_bundle_leak_audit_script(
        managed_bundle_names=("Cursor.app",),
        package_paths=(str(managed_package),),
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", f"set -euo pipefail\n{script}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert (
        "Managed macOS app bundles must not be exposed through home.packages."
        in result.stderr
    )
    assert f" - Cursor.app <= {managed_package}" in result.stderr


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_profile_bundle_leak_audit_script_ignores_unmanaged_bundle_exposure(
    tmp_path: Path,
) -> None:
    """Activation audit should ignore unrelated GUI bundles in package outputs."""
    unrelated_package = tmp_path / "spotify-package"
    (unrelated_package / "Applications" / "Spotify.app").mkdir(parents=True)

    script = _profile_bundle_leak_audit_script(
        managed_bundle_names=("Cursor.app",),
        package_paths=(str(unrelated_package),),
    )
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "-c", f"set -euo pipefail\n{script}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_reports_conflicting_package_lists() -> None:
    """Managed app packages should not overlap with installed package lists."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake \"nixpkgs\";
          pkgs = import nixpkgs {{ system = \"aarch64-darwin\"; }};
          macApps = import {root}/lib/mac-apps.nix {{ inherit (pkgs) lib; inherit pkgs; }};
          managedPkg = {{
            pname = \"cursor\";
            outPath = \"/nix/store/fake-cursor\";
            passthru.macApp = {{
              bundleName = \"Cursor.app\";
              bundleRelPath = \"Applications/Cursor.app\";
            }};
          }};
        in
          builtins.toJSON (macApps.managedAppsNotInPackageListsAssertion {{
            entries = [ {{ package = managedPkg; bundleName = \"Cursor.app\"; mode = \"copy\"; }} ];
            packageLists = [
              {{
                label = \"home.packages\";
                packages = [
                  {{
                    pname = \"cursor-wrapper\";
                    outPath = \"/nix/store/fake-wrapper\";
                    passthru.macApp.bundleName = \"Cursor.app\";
                  }}
                ];
              }}
            ];
          }})
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "assertion": False,
        "message": (
            "nixcfg.macApps.systemApplications packages must not also appear in other "
            "installed package lists.\n"
            "- Cursor.app (cursor) also appears in home.packages as cursor-wrapper."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_app_overlap_assertion_allows_distinct_package_lists() -> None:
    """Distinct package lists should not trip the managed app overlap assertion."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake \"nixpkgs\";
          pkgs = import nixpkgs {{ system = \"aarch64-darwin\"; }};
          macApps = import {root}/lib/mac-apps.nix {{ inherit (pkgs) lib; inherit pkgs; }};
          managedPkg = {{
            pname = \"cursor\";
            outPath = \"/nix/store/fake-cursor\";
            passthru.macApp = {{
              bundleName = \"Cursor.app\";
              bundleRelPath = \"Applications/Cursor.app\";
            }};
          }};
        in
          builtins.toJSON (macApps.managedAppsNotInPackageListsAssertion {{
            entries = [ {{ package = managedPkg; bundleName = \"Cursor.app\"; mode = \"copy\"; }} ];
            packageLists = [
              {{
                label = \"home.packages\";
                packages = [
                  {{
                    pname = \"spotify\";
                    outPath = \"/nix/store/fake-spotify\";
                    passthru.macApp.bundleName = \"Spotify.app\";
                  }}
                ];
              }}
            ];
          }})
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "assertion": True,
        "message": (
            "nixcfg.macApps.systemApplications packages must not also appear in other "
            "installed package lists."
        ),
    }


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_zoom_overlay_pins_newer_darwin_version_and_mac_app_metadata() -> None:
    """The local Zoom overlay should pin a newer macOS build and expose macApp metadata."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          nixpkgs = builtins.getFlake \"nixpkgs\";
          prev = import nixpkgs {{ system = \"aarch64-darwin\"; config.allowUnfree = true; }};
          selfSource = builtins.fromJSON (builtins.readFile {root}/overlays/zoom-us.sources.json);
          overlay = import {root}/overlays/zoom-us.nix {{
            inherit prev selfSource;
            system = \"aarch64-darwin\";
          }};
        in
          builtins.toJSON {{
            version = overlay.zoom-us.version;
            macApp = overlay.zoom-us.passthru.macApp;
          }}
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "version": "7.0.0.77593",
        "macApp": {
            "bundleName": "zoom.us.app",
            "bundleRelPath": "Applications/zoom.us.app",
            "installMode": "copy",
        },
    }


def test_george_config_manages_mutable_gui_apps_via_system_applications() -> None:
    """George's config should route the known-problem mutable app bundles via /Applications."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)

    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(package_sets.values, "excludePackagesByName").value,
        """
        [
          "chatgpt"
          "cursor"
          "datagrip"
          "wispr-flow"
        ]
        """,
    )

    mac_apps = expect_instance(
        expect_binding(nixcfg.values, "macApps").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_apps.values, "systemApplications").value,
        """
        [
          {
            package = pkgs.chatgpt;
            mode = "copy";
          }
          {
            package = pkgs.code-cursor;
            mode = "copy";
          }
          {
            package = pkgs.jetbrains.datagrip;
            mode = "copy";
          }
          {
            package = pkgs.vscode-insiders;
            mode = "copy";
          }
          { package = pkgs.wispr-flow; }
          { package = pkgs.zoom-us; }
        ]
        """,
    )

    programs = expect_instance(
        expect_binding(root.values, "programs").value, AttributeSet
    )
    vscode = expect_instance(
        expect_binding(programs.values, "vscode").value, AttributeSet
    )
    assert_nix_ast_equal(expect_binding(vscode.values, "package").value, "null")
    assert_nix_ast_equal(
        expect_binding(vscode.values, "pname").value, '"vscode-insiders"'
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_managed_gui_app_overlays_expose_copy_mode_mac_app_metadata() -> None:
    """Managed GUI overlays should expose copy-mode macApp metadata for /Applications."""
    root = Path(REPO_ROOT).resolve()
    nix = shutil.which("nix")
    assert nix is not None
    expr = textwrap.dedent(
        f"""
        let
          flake = builtins.getFlake (toString {root});
          pkgs = flake.darwinConfigurations.argus.pkgs;
        in
          builtins.toJSON {{
            chatgpt = pkgs.chatgpt.passthru.macApp;
            cursor = pkgs.code-cursor.passthru.macApp;
            datagrip = pkgs.jetbrains.datagrip.passthru.macApp;
            vscodeInsiders = pkgs.vscode-insiders.passthru.macApp;
          }}
        """
    )
    result = subprocess.run(  # noqa: S603
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "chatgpt": {
            "bundleName": "ChatGPT.app",
            "bundleRelPath": "Applications/ChatGPT.app",
            "installMode": "copy",
        },
        "cursor": {
            "bundleName": "Cursor.app",
            "bundleRelPath": "Applications/Cursor.app",
            "installMode": "copy",
        },
        "datagrip": {
            "bundleName": "DataGrip.app",
            "bundleRelPath": "Applications/DataGrip.app",
            "installMode": "copy",
        },
        "vscodeInsiders": {
            "bundleName": "Visual Studio Code - Insiders.app",
            "bundleRelPath": "Applications/Visual Studio Code - Insiders.app",
            "installMode": "copy",
        },
    }


def test_dock_configs_keep_the_targeted_gc_mitigation_scope_explicit() -> None:
    """Dock modules should keep the targeted /Applications policy explicit for managed bundles."""
    george_dock = (REPO_ROOT / "modules/darwin/george/dock-apps.nix").read_text(
        encoding="utf-8"
    )
    town_dock = (REPO_ROOT / "modules/darwin/george/town-dock-apps.nix").read_text(
        encoding="utf-8"
    )

    for rendered in (george_dock, town_dock):
        assert "intentionally left profile-managed" in rendered
        assert (
            "/Users/${primaryUser}/Applications/Home Manager Apps/ChatGPT.app"
            not in rendered
        )
        assert (
            "/Users/${primaryUser}/Applications/Home Manager Apps/DataGrip.app"
            not in rendered
        )

    assert '"/Applications/ChatGPT.app"' in george_dock
    assert '"/Applications/DataGrip.app"' in george_dock

    assert '"/Applications/ChatGPT.app"' in town_dock
    assert '"/Applications/Cursor.app"' in town_dock
    assert '"/Applications/Visual Studio Code - Insiders.app"' in town_dock
    assert '"/Applications/DataGrip.app"' in town_dock
    assert (
        "/Users/${primaryUser}/Applications/Home Manager Apps/Cursor.app"
        not in town_dock
    )
    assert (
        "/Users/${primaryUser}/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"
        not in town_dock
    )


def test_george_bin_wrappers_target_only_managed_app_copies() -> None:
    """CLI wrappers should only target the managed /Applications bundles."""
    cursor_wrapper = (REPO_ROOT / "home/george/bin/cursor").read_text(encoding="utf-8")
    code_insiders_wrapper = (REPO_ROOT / "home/george/bin/code-insiders").read_text(
        encoding="utf-8"
    )

    assert (
        '"/Applications/Cursor.app/Contents/Resources/app/bin/cursor"' in cursor_wrapper
    )
    assert "$HOME/Applications/Home Manager Apps/Cursor.app" not in cursor_wrapper
    assert 'exec "$candidate" "$@"' in cursor_wrapper
    assert (
        '"/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code"'
        in code_insiders_wrapper
    )
    assert (
        "$HOME/Applications/Home Manager Apps/Visual Studio Code - Insiders.app"
        not in code_insiders_wrapper
    )
    assert 'exec "$candidate" "$@"' in code_insiders_wrapper
