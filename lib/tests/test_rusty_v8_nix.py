"""Structural contracts for the shared rusty-v8 builder."""

from collections.abc import Iterator
from typing import TYPE_CHECKING

from nix_manipulator import parse

from lib.tests._nix_ast import assert_nix_ast_equal, parse_nix_expr
from lib.tests._nix_source import nix_file_binding_expr, nix_source_fragment_expr
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from tree_sitter import Node


def _nodes(root: Node) -> Iterator[Node]:
    yield root
    for child in root.named_children:
        yield from _nodes(child)


def test_rusty_v8_derives_clang_resource_version_from_immutable_source() -> None:
    """Concrete callers provide source identity, not duplicated Clang metadata."""
    source = (REPO_ROOT / "lib/rusty-v8.nix").read_text(encoding="utf-8")
    encoded = source.encode()
    formals = [
        node
        for node in _nodes(parse(source).node)
        if node.type == "formal"
        and node.named_children
        and node.named_children[0].type == "identifier"
        and encoded[
            node.named_children[0].start_byte : node.named_children[0].end_byte
        ].decode()
        in {"clangResourceVersion", "rustyV8Src"}
    ]

    formal_names = [
        encoded[
            formal.named_children[0].start_byte : formal.named_children[0].end_byte
        ].decode()
        for formal in formals
    ]
    assert formal_names == ["rustyV8Src"]
    assert [child.type for child in formals[0].named_children] == ["identifier"]


def test_rusty_v8_links_compiler_rt_at_chromiums_builtins_path() -> None:
    """Goose and Codex share the builder, and both look up compiler-rt by Chromium's names."""
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/rusty-v8.nix", "clangRuntimeLink"),
        """
        if pkgs.stdenv.hostPlatform.isLinux then
          ''
            runtime_dir="$version_dir/lib/${pkgs.stdenv.hostPlatform.config}"
            rm -rf "$runtime_dir"
            mkdir -p "$runtime_dir"
            ln -s ${pkgs.llvmPackages.compiler-rt}/lib/linux/libclang_rt.builtins-${pkgs.stdenv.hostPlatform.parsed.cpu.name}${pkgs.stdenv.hostPlatform.extensions.staticLibrary} \\
              "$runtime_dir/libclang_rt.builtins${pkgs.stdenv.hostPlatform.extensions.staticLibrary}"
          ''
        else if pkgs.stdenv.hostPlatform.isDarwin then
          ''
            runtime_dir="$version_dir/lib/darwin"
            rm -rf "$runtime_dir"
            mkdir -p "$runtime_dir"
            if [ -d ${pkgs.llvmPackages.clang}/resource-root/lib/darwin ]; then
              for entry in ${pkgs.llvmPackages.clang}/resource-root/lib/darwin/*; do
                [ -e "$entry" ] || continue
                ln -s "$entry" "$runtime_dir/$(basename "$entry")"
              done
            fi
            ln -sfn ${pkgs.llvmPackages.compiler-rt}/lib/darwin/libclang_rt.osx${pkgs.stdenv.hostPlatform.extensions.staticLibrary} \\
              "$runtime_dir/libclang_rt.osx${pkgs.stdenv.hostPlatform.extensions.staticLibrary}"
          ''
        else
          abort "rusty-v8 has no compiler-rt builtins layout for this platform"
        """,
    )


def test_rusty_v8_linux_gn_can_run_pkg_config() -> None:
    """The Linux compiler config probes glib, so the native build needs pkg-config."""
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "lib/rusty-v8.nix",
            "lib.optionals pkgs.stdenv.hostPlatform.isLinux ",
            ";",
        ),
        parse_nix_expr("[ pkgs.pkg-config ]"),
    )
    assert_nix_ast_equal(
        nix_source_fragment_expr(
            "lib/rusty-v8.nix",
            "lib.optionals pkgs.stdenv.hostPlatform.isLinux ",
            ";",
            occurrence=1,
        ),
        parse_nix_expr("[ pkgs.glib ]"),
    )
