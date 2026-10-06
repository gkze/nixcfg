"""Structural tests for the Zed nightly package contracts."""

from typing import TYPE_CHECKING

from nix_manipulator import parse
from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_binding_expr
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from tree_sitter import Node

_PACKAGE = "packages/zed-editor-nightly/default.nix"
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
    """Darwin cctools strip removes .rmeta from rustc rlibs (nixpkgs#218712)."""
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
          wrap =
            inner:
            {
              __functor = self: args: applyDarwinRlibMetadata (inner args);
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
