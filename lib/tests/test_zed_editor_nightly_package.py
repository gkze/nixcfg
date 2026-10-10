"""Structural tests for the Zed nightly package contracts."""

from typing import TYPE_CHECKING

import pytest
from nix_manipulator import parse
from nix_manipulator.expressions.binary import BinaryExpression
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.let import LetExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_binding_expr
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from tree_sitter import Node

_PACKAGE = "packages/zed-editor-nightly/default.nix"
_POLICY = "packages/zed-editor-nightly/crate-cache-policy.nix"
_DELETED_LIVEKIT_PATH = "livekit-libwebrtc/package.nix"


def test_zed_nightly_uses_nixpkgs_livekit_libwebrtc() -> None:
    """Zed #65203 deleted the vendored package; follow nixpkgs instead of pinning."""
    assert_nix_ast_equal(
        nix_file_binding_expr(_PACKAGE, "livekitLibwebrtc"),
        """pkgs.livekit-libwebrtc.overrideAttrs (
          old:
          lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
            NIX_LDFLAGS = (old.NIX_LDFLAGS or "") + " -rpath ${lib.makeLibraryPath [ libglvnd ]}";
            dontPatchELF = true;
          }
        )""",
    )


def test_zed_nightly_build_rust_crate_is_overridable_functor() -> None:
    """crate2nix needs (buildRustCrateForPkgs pkgs).override; a lambda fails eval."""
    expr = nix_file_binding_expr(_PACKAGE, "zedBuildRustCrate")
    assert not isinstance(expr, FunctionDefinition)
    assert_nix_ast_equal(
        expr,
        """
        (import ./build-rust-crate.nix { inherit lib; }).wrapBuildRustCrate (
          pkgs.buildRustCrate.override {
            cargo = rustToolchain;
            rustc = rustToolchain;
          }
        ) pkgs.stdenv.hostPlatform.isDarwin
        """,
    )


def test_zed_nightly_preserves_darwin_rlib_metadata() -> None:
    """Darwin keeps rlib metadata; Linux rust_zed unsplits out+lib (nixpkgs#218712)."""
    assert_nix_ast_equal(
        nix_file_binding_expr(
            "packages/zed-editor-nightly/build-rust-crate.nix",
            "wrapBuildRustCrate",
        ),
        """
        builder: isDarwin:
        let
          applyDarwinRlibMetadata =
            drv:
            drv.overrideAttrs (
              _old:
              lib.optionalAttrs isDarwin {
                dontStrip = true;
                stripExclude = [ "*.rlib" ];
              }
            );
          linuxZedUnsplit =
            args:
            args
            // lib.optionalAttrs (!isDarwin && (args.crateName or "") == "zed") {
              outputs = [ "out" ];
              outputDev = [ "out" ];
            };
          wrap = inner: {
            __functor =
              _self: args:
              applyDarwinRlibMetadata (inner (linuxZedUnsplit args));
            override = f: wrap (inner.override f);
          };
        in
        wrap builder
        """,
    )


def test_zed_nightly_does_not_import_deleted_livekit_libwebrtc_path() -> None:
    """Interpolating the deleted flake-input path fails eval on every platform."""
    source = (REPO_ROOT / _PACKAGE).read_text(encoding="utf-8")
    encoded = source.encode()
    leftovers: list[tuple[str, str]] = []

    def visit(node: Node) -> None:
        if node.type in {"path_expression", "interpolation"}:
            text = encoded[node.start_byte : node.end_byte].decode()
            if _DELETED_LIVEKIT_PATH in text:
                leftovers.append((node.type, text))
        for child in node.named_children:
            visit(child)

    visit(parse(source).node)
    assert leftovers == []


def test_zed_linux_x11_and_fontconfig_sys_are_pkgconfig_leaf_crates() -> None:
    """validate-x86 rust_x11 / yeslogic-fontconfig-sys / webrtc-sys run pkg-config in build.rs."""
    assert_nix_ast_equal(
        nix_file_binding_expr(_POLICY, "pkgConfigConsumers"),
        """[
          "webrtc-sys"
          "x11"
          "yeslogic-fontconfig-sys"
          "zed"
        ]""",
    )
    assert_nix_ast_equal(
        nix_file_binding_expr(_POLICY, "x11LibraryConsumers"),
        '[ "x11" ]',
    )
    assert_nix_ast_equal(
        nix_file_binding_expr(_POLICY, "fontconfigSysConsumers"),
        '[ "yeslogic-fontconfig-sys" ]',
    )
    assert_nix_ast_equal(
        nix_file_binding_expr(_POLICY, "webrtcSysLibraryConsumers"),
        '[ "webrtc-sys" ]',
    )


def _binding_from_override(expr: object, name: str) -> object:
    if isinstance(expr, LetExpression):
        return _binding_from_override(expr.value, name)
    if isinstance(expr, AttributeSet):
        return expect_binding(expr.values, name).value
    if isinstance(expr, BinaryExpression):
        try:
            return _binding_from_override(expr.left, name)
        except AssertionError:
            return _binding_from_override(expr.right, name)
    msg = f"missing binding {name} on {type(expr).__name__}"
    raise AssertionError(msg)


def test_zed_scoped_override_adds_linux_x11_and_fontconfig_libraries() -> None:
    """Leaf crates get pkg-config's probed lib, not the full Zed system dump."""
    override = expect_instance(
        nix_file_binding_expr(_PACKAGE, "scopedOverride"),
        FunctionDefinition,
    )
    build_inputs = _binding_from_override(override.output, "buildInputs")
    assert_nix_ast_equal(
        build_inputs,
        """
        (attrs.buildInputs or [ ])
        ++ lib.optionals (builtins.elem crateName crateCachePolicy.systemLibraryConsumers) zedBuildInputs
        ++
          lib.optionals
            (pkgs.stdenv.hostPlatform.isLinux && builtins.elem crateName crateCachePolicy.x11LibraryConsumers)
            [
              libx11
            ]
        ++
          lib.optionals
            (
              pkgs.stdenv.hostPlatform.isLinux
              && builtins.elem crateName crateCachePolicy.fontconfigSysConsumers
            )
            [
              fontconfig
            ]
        ++
          lib.optionals
            (
              pkgs.stdenv.hostPlatform.isLinux
              && builtins.elem crateName crateCachePolicy.webrtcSysLibraryConsumers
            )
            [
              glib
            ]
        ++ lib.optionals (builtins.elem crateName darwinWorkspaceCrates) darwinWorkspaceBuildInputs
        """,
    )


def test_zed_project_overrides_do_not_special_case_agent_ui() -> None:
    """Intern fix is a same-slot language_models --rebuild, not an override."""
    overrides = nix_file_binding_expr(_PACKAGE, "projectCrateOverrides")
    with pytest.raises(AssertionError, match="missing binding agent_ui"):
        _binding_from_override(overrides, "agent_ui")
    with pytest.raises(
        AssertionError, match="missing binding agentUiLocatorDiagnostic"
    ):
        _binding_from_override(overrides, "agentUiLocatorDiagnostic")


def test_zed_scoped_crates_include_linux_pkgconfig_leaf_consumers() -> None:
    """x11 and yeslogic-fontconfig-sys must receive scopedOverride on Linux."""
    assert_nix_ast_equal(
        nix_file_binding_expr(_PACKAGE, "scopedCrates"),
        """
        lib.unique (
          builtins.attrNames crateSourcePreparations
          ++ crateCachePolicy.bindgenConsumers
          ++ crateCachePolicy.commitShaConsumers
          ++ crateCachePolicy.fontConfigConsumers
          ++ crateCachePolicy.lldConsumers
          ++ crateCachePolicy.livekitWebrtcConsumers
          ++ crateCachePolicy.fontconfigSysConsumers
          ++ crateCachePolicy.pkgConfigConsumers
          ++ crateCachePolicy.protocConsumers
          ++ crateCachePolicy.releaseVersionConsumers
          ++ crateCachePolicy.systemLibraryConsumers
          ++ crateCachePolicy.updateExplanationConsumers
          ++ crateCachePolicy.webrtcSysLibraryConsumers
          ++ crateCachePolicy.x11LibraryConsumers
          ++ crateCachePolicy.xcodebuildConsumers
          ++ crateCachePolicy.zstdPkgConfigConsumers
          ++ darwinWorkspaceCrates
        )
        """,
    )
