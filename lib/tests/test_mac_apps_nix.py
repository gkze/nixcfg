"""Regression checks for macOS application bundle management."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from functools import cache
from pathlib import Path

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
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


def test_george_config_manages_wispr_flow_via_system_applications_only() -> None:
    """George's config should avoid duplicate Wispr Flow installs."""
    root = _module_output("home/george/configuration.nix")
    nixcfg = expect_instance(expect_binding(root.values, "nixcfg").value, AttributeSet)

    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(package_sets.values, "excludePackagesByName").value,
        '[ "wispr-flow" ]',
    )

    mac_apps = expect_instance(
        expect_binding(nixcfg.values, "macApps").value,
        AttributeSet,
    )
    assert_nix_ast_equal(
        expect_binding(mac_apps.values, "systemApplications").value,
        "[ { package = pkgs.wispr-flow; } ]",
    )
