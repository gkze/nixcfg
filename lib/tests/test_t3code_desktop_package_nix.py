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
from lib.tests._shell_ast import command_texts, parse_shell
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


def test_t3code_desktop_package_keeps_staged_runtime_and_electron_dist() -> None:
    """Desktop packaging should use the centrally packaged Electron runtime."""
    assert_nix_ast_equal(_desktop_assertion().expression, "versionSyncCheck")
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "appName").value,
        '"T3 Code (Alpha)"',
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronRuntime").value,
        "nixcfgElectron.runtimeFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronRuntimeVersion").value,
        "electronRuntime.version",
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronHeaders").value,
        "electronRuntime.passthru.headers",
    )
    assert_nix_ast_equal(
        expect_scope_binding(_desktop_assertion(), "electronDist").value,
        "electronRuntime.passthru.dist",
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

    build_phase = expect_instance(
        expect_binding(_desktop_derivation_args().values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(build_phase.value)
    build_commands = command_texts(build_shell)
    for command in (
        'export HOME="$TMPDIR/home"',
        'export BUN_INSTALL_CACHE_DIR="$TMPDIR/.bun-cache"',
        'export NODE_COMPILE_CACHE="$TMPDIR/node-compile-cache"',
        "export NODE_DISABLE_COMPILE_CACHE=1",
        'mkdir -p "$appBuildRoot"',
        'cd "$appBuildRoot"',
    ):
        assert command in build_commands
    electron_builder = command_texts(
        build_shell, "./node_modules/.bin/electron-builder"
    )
    assert len(electron_builder) == 1
    assert "-c.npmRebuild=false" in electron_builder[0]

    for required_contract in (
        'T3CODE_APP_ASAR="$appAsar"',
        "@electron+asar@",
        "createPackageFromFiles(",
        '{ unpack: "*.node" },',
        'appUnpacked = appAsar + ".unpacked"',
        "${../../lib/asar_integrity.py}",
        "set-info-plist-hash",
    ):
        assert required_contract in build_phase.value

    install_phase = expect_instance(
        expect_binding(_desktop_derivation_args().values, "installPhase").value,
        IndentedString,
    )
    for snippet in (
        "${lib.getExe python3} ${./patch_info_plist.py}",
        "--app-name ${lib.escapeShellArg appName}",
        "--bundle-id ${lib.escapeShellArg appId}",
        "--url-scheme ${lib.escapeShellArg appProtocolScheme}",
    ):
        assert snippet in install_phase.value
    install_shell = parse_shell(install_phase.value)
    assert command_texts(install_shell, "makeWrapper") == [
        "makeWrapper \\\n"
        '      "$out/Applications/__NIX_INTERP__/Contents/MacOS/__NIX_INTERP__" \\\n'
        '      "$out/bin/__NIX_INTERP__"'
    ]
    install_check_phase = expect_instance(
        expect_binding(_desktop_derivation_args().values, "installCheckPhase").value,
        IndentedString,
    )
    install_check_shell = parse_shell(install_check_phase.value)
    electron_smokes = [
        command
        for command in command_texts(install_check_shell)
        if command.startswith("ELECTRON_RUN_AS_NODE=1")
    ]
    assert len(electron_smokes) == 1
    assert "check-info-plist-hash" in install_check_phase.value
    assert "app.asar.unpacked" in install_check_phase.value
    assert "app.asar/node_modules/node-pty" in install_check_phase.value
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
    assert isinstance(payload.get("version"), str)
    hashes = payload["hashes"]
    assert len(hashes) == 1
    [hash_entry] = hashes
    assert set(hash_entry) == {"hash", "hashType", "platform"}
    assert hash_entry["hashType"] == "nodeModulesHash"
    assert hash_entry["platform"] == "aarch64-darwin"
    hash_value = hash_entry["hash"]
    assert isinstance(hash_value, str)
    assert hash_value.startswith("sha256-")
