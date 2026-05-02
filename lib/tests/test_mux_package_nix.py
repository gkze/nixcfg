"""AST-level tests for mux's Darwin packaging contract."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
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
def _mux_derivation() -> FunctionCall:
    """Parse mux's package expression once and return the top-level derivation."""
    root = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "packages/mux/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, FunctionCall)


def test_mux_top_level_derivation_uses_stdenv_mk_derivation() -> None:
    """Mux should still be packaged as one top-level stdenv.mkDerivation."""
    assert_nix_ast_equal(_mux_derivation().name, "stdenv.mkDerivation")


def test_mux_uses_central_electron_runtime_artifacts() -> None:
    """Mux should source Electron runtime, headers, and dist from nixcfgElectron."""
    derivation = _mux_derivation()

    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronVersion").value,
        '"38.7.2"',
    )
    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronRuntime").value,
        "nixcfgElectron.runtimeFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronHeaders").value,
        "electronRuntime.passthru.headers",
    )
    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronDist").value,
        "electronRuntime.passthru.dist",
    )


def test_mux_linux_desktop_item_is_structured() -> None:
    """Linux desktop metadata should use makeDesktopItem instead of a heredoc."""
    desktop_item = expect_instance(
        expect_scope_binding(_mux_derivation(), "linuxDesktopItem").value,
        FunctionCall,
    )
    desktop_item_args = expect_instance(desktop_item.argument, AttributeSet)

    assert_nix_ast_equal(desktop_item.name, "makeDesktopItem")
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "desktopName").value,
        '"Mux"',
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "genericName").value,
        '"Agent Multiplexer"',
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "exec").value,
        '"${pname} %U"',
    )


def test_mux_derivation_encodes_the_hermetic_darwin_packaging_contract() -> None:
    """Mux should keep the Darwin hermeticity fixes wired into the derivation."""
    derivation_args = expect_instance(_mux_derivation().argument, AttributeSet)

    configure_phase = expect_instance(
        expect_binding(derivation_args.values, "configurePhase").value,
        IndentedString,
    )
    assert 'export npm_config_nodedir="${electronHeaders}"' in configure_phase.value
    assert (
        'if [ "$resolvedElectronVersion" != "${electronVersion}" ]; then'
        in configure_phase.value
    )
    assert "./scripts/postinstall.sh" in configure_phase.value

    post_patch = expect_instance(
        expect_binding(derivation_args.values, "postPatch").value,
        FunctionCall,
    )
    optional_string = expect_instance(post_patch.name, FunctionCall)
    assert_nix_ast_equal(optional_string.name, "lib.optionalString")
    assert_nix_ast_equal(optional_string.argument, "stdenv.hostPlatform.isDarwin")
    post_patch_script = expect_instance(post_patch.argument, IndentedString)
    assert "patch_package_json.py" in post_patch_script.value
    assert "package.json" in post_patch_script.value
    assert "electron-dist" in post_patch_script.value

    build_phase = expect_instance(
        expect_binding(derivation_args.values, "buildPhase").value,
        IfExpression,
    )
    assert_nix_ast_equal(build_phase.condition, "stdenv.hostPlatform.isDarwin")
    darwin_build = expect_instance(build_phase.consequence, IndentedString)
    assert "bun scripts/generate-icons.ts png icns linux-icons" in darwin_build.value
    assert "bun x electron-builder" in darwin_build.value
    assert 'cp -R ${electronDist}/. "$electronDistDir"/' in darwin_build.value
    assert '-c.electronDist="$electronDistDir"' in darwin_build.value
    assert (
        "-c.electronVersion=${lib.escapeShellArg electronRuntimeVersion}"
        in darwin_build.value
    )
    assert "-c.mac.identity=null" in darwin_build.value
