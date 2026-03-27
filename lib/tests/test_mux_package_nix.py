"""AST-level tests for mux's Darwin packaging contract."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.select import Select
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import check, expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
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


def test_mux_declares_local_darwin_electron_artifacts() -> None:
    """Mux should declare local Electron headers, zip, and dist derivations."""
    derivation = _mux_derivation()

    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronVersion").value,
        '"38.7.2"',
    )

    electron_headers = expect_instance(
        expect_scope_binding(derivation, "electronHeaders").value,
        FunctionCall,
    )
    assert_nix_ast_equal(electron_headers.name, "stdenvNoCC.mkDerivation")
    headers_args = expect_instance(electron_headers.argument, AttributeSet)
    headers_src = expect_instance(
        expect_binding(headers_args.values, "src").value, FunctionCall
    )
    assert_nix_ast_equal(headers_src.name, "fetchurl")
    headers_src_args = expect_instance(headers_src.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(headers_src_args.values, "url").value,
        '"https://www.electronjs.org/headers/v${electronVersion}/node-v${electronVersion}-headers.tar.gz"',
    )
    assert_nix_ast_equal(
        expect_binding(headers_src_args.values, "sha256").value,
        "hashToNix32 electronHeadersChecksum",
    )

    electron_zip = expect_instance(
        expect_scope_binding(derivation, "electronZip").value,
        FunctionCall,
    )
    assert_nix_ast_equal(electron_zip.name, "fetchurl")
    electron_zip_args = expect_instance(electron_zip.argument, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(electron_zip_args.values, "url").value,
        '"https://github.com/electron/electron/releases/download/v${electronVersion}/electron-v${electronVersion}-${electronZipPlatform}.zip"',
    )
    assert_nix_ast_equal(
        expect_binding(electron_zip_args.values, "sha256").value,
        "hashToNix32 electronZipChecksum",
    )

    electron_dist = expect_instance(
        expect_scope_binding(derivation, "electronDist").value,
        FunctionCall,
    )
    assert_nix_ast_equal(electron_dist.name, "stdenvNoCC.mkDerivation")
    electron_dist_args = expect_instance(electron_dist.argument, AttributeSet)
    install_phase = expect_instance(
        expect_binding(electron_dist_args.values, "installPhase").value,
        IndentedString,
    )
    check(
        'ln -s ${electronZip} "$out/electron-v${electronVersion}-${electronZipPlatform}.zip"'
        in install_phase.value
    )


def test_mux_keeps_darwin_platform_mapping_and_checksum_table() -> None:
    """Mux should keep both Darwin Electron zip mappings in the package AST."""
    derivation = _mux_derivation()

    assert_nix_ast_equal(
        expect_scope_binding(derivation, "electronZipPlatform").value,
        """if stdenv.hostPlatform.system == "aarch64-darwin" then
  "darwin-arm64"
else if stdenv.hostPlatform.system == "x86_64-darwin" then
  "darwin-x64"
else
  throw "packages/mux/default.nix unsupported Darwin platform ${stdenv.hostPlatform.system}"
""",
    )

    electron_zip_checksum = expect_instance(
        expect_scope_binding(derivation, "electronZipChecksum").value,
        Select,
    )
    check(
        electron_zip_checksum.attribute == "${electronVersion}.${electronZipPlatform}"
    )

    checksum_bindings = binding_map(
        expect_instance(electron_zip_checksum.expression, AttributeSet).values,
    )
    version_hashes = expect_instance(
        next(iter(checksum_bindings.values())).value, AttributeSet
    )
    platform_hashes = binding_map(version_hashes.values)
    check(set(platform_hashes) == {'"darwin-arm64"', '"darwin-x64"'})

    assert_nix_ast_equal(
        electron_zip_checksum.default,
        (
            'throw "packages/mux/default.nix missing Electron zip hash '
            'for ${electronVersion}/${electronZipPlatform}"'
        ),
    )


def test_mux_derivation_encodes_the_hermetic_darwin_packaging_contract() -> None:
    """Mux should keep the Darwin hermeticity fixes wired into the derivation."""
    derivation_args = expect_instance(_mux_derivation().argument, AttributeSet)

    configure_phase = expect_instance(
        expect_binding(derivation_args.values, "configurePhase").value,
        IndentedString,
    )
    check('export npm_config_nodedir="${electronHeaders}"' in configure_phase.value)
    check(
        'if [ "$resolvedElectronVersion" != "${electronVersion}" ]; then'
        in configure_phase.value
    )
    check("./scripts/postinstall.sh" in configure_phase.value)

    post_patch = expect_instance(
        expect_binding(derivation_args.values, "postPatch").value,
        FunctionCall,
    )
    optional_string = expect_instance(post_patch.name, FunctionCall)
    assert_nix_ast_equal(optional_string.name, "lib.optionalString")
    assert_nix_ast_equal(optional_string.argument, "stdenv.hostPlatform.isDarwin")
    post_patch_script = expect_instance(post_patch.argument, IndentedString)
    check('build["electronDist"] = "${electronDist}"' in post_patch_script.value)
    check('mac["target"] = "dir"' in post_patch_script.value)
    check('mac["hardenedRuntime"] = False' in post_patch_script.value)
    check('mac["notarize"] = False' in post_patch_script.value)

    build_phase = expect_instance(
        expect_binding(derivation_args.values, "buildPhase").value,
        IfExpression,
    )
    assert_nix_ast_equal(build_phase.condition, "stdenv.hostPlatform.isDarwin")
    darwin_build = expect_instance(build_phase.consequence, IndentedString)
    check("bun scripts/generate-icons.ts png icns linux-icons" in darwin_build.value)
    check(
        "bun x electron-builder --mac --dir --publish never -c.mac.identity=null"
        in darwin_build.value
    )
