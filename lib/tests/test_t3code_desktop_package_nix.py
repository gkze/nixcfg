"""AST-level checks for the T3 Code desktop package."""

from __future__ import annotations

import json
from functools import cache

from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _desktop_assertion() -> Assertion:
    """Return the top-level version assertion from the desktop package."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/t3code-desktop/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, Assertion)


@cache
def _desktop_derivation_args() -> AttributeSet:
    """Return the attrset passed to the desktop derivation."""
    body = expect_instance(_desktop_assertion().body, FunctionCall)
    return expect_instance(body.argument, AttributeSet)


def test_t3code_desktop_package_keeps_staged_runtime_and_darwin_electron_zip() -> None:
    """Desktop packaging should keep staged runtime metadata and a local Electron dist."""
    assert_nix_ast_equal(_desktop_assertion().expression, "versionSyncCheck")
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "appName").value,
        '"T3 Code (Alpha)"',
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronTarget").value,
        """
        {
          aarch64-darwin = "darwin-arm64";
        }
        .${system}
          or (throw "packages/t3code-desktop/default.nix unsupported Darwin platform ${system}")
        """,
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronZip").value,
        """
        fetchurl {
          url = "https://github.com/electron/electron/releases/download/v${electronVersion}/electron-v${electronVersion}-${electronTarget}.zip";
          hash = darwinElectronZipHash;
        }
        """,
    )

    node_modules = expect_instance(
        expect_scope_binding(_desktop_assertion(), "node_modules").value,
        FunctionCall,
    )
    node_modules_args = expect_instance(node_modules.argument, AttributeSet)
    node_modules_build = expect_instance(
        expect_binding(node_modules_args.values, "buildPhase").value,
        IndentedString,
    )
    for snippet in (
        "${lib.getExe python3} ${./render_runtime_package_json.py}",
        "--electron-builder-version ${lib.escapeShellArg electronBuilderVersion}",
        "--commit-hash ${lib.escapeShellArg t3codeCommitHash}",
        "cp ${./bun.lock} bun.lock",
        "bun install",
    ):
        assert snippet in node_modules_build.value
    assert_nix_ast_equal(
        expect_binding(node_modules_args.values, "outputHash").value,
        'outputs.lib.sourceHashForPlatform pname "nodeModulesHash" system',
    )

    install_phase = expect_instance(
        expect_binding(_desktop_derivation_args().values, "installPhase").value,
        IndentedString,
    )
    for snippet in (
        "${lib.getExe python3} ${./patch_info_plist.py}",
        "--app-name ${lib.escapeShellArg appName}",
        "--bundle-id ${lib.escapeShellArg appId}",
        "--url-scheme ${lib.escapeShellArg appProtocolScheme}",
        'exec "$out/Applications/${appBundleName}/Contents/MacOS/${appName}" "$@"',
    ):
        assert snippet in install_phase.value
    passthru = expect_instance(
        expect_binding(_desktop_derivation_args().values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value, AttributeSet
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )


def test_t3code_desktop_sources_track_the_supported_platform_matrix() -> None:
    """Persisted hashes should stay aligned with the desktop package platform list."""
    payload = json.loads(
        (REPO_ROOT / "packages/t3code-desktop/sources.json").read_text(encoding="utf-8")
    )

    assert payload["input"] == "t3code"
    assert payload["version"] == "main"
    assert payload["hashes"] == [
        {
            "hash": "sha256-T75MRSMcrJrgCnLTDc05slg/dyIFED4rLBXCqNrb0yU=",
            "hashType": "nodeModulesHash",
            "platform": "aarch64-darwin",
        }
    ]
