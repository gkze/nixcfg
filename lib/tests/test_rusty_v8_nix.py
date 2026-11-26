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


def test_rusty_v8_keeps_trap_only_ubsan_off_the_sanitizer_runtime() -> None:
    """Goose and Codex share the clang that must not turn trap checks into UBSan."""
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/rusty-v8.nix", "clangLink"),
        r"""
        ''
          if printf 'int nixcfg_ubsan_probe;\n' | ${pkgs.llvmPackages.clang}/bin/clang \
            -fsanitize=return \
            -fsanitize-trap=return \
            -fsanitize-ignore-for-ubsan-feature=return \
            -x c -c -o /dev/null - >/dev/null 2>&1
          then
            ln -sf ${pkgs.llvmPackages.clang}/bin/clang $llvm_bin/clang
            ln -sf ${pkgs.llvmPackages.clang}/bin/clang++ $llvm_bin/clang++
          else
            for tool in clang clang++; do
              cat > $llvm_bin/$tool <<PY
              #!${pkgs.python3}/bin/python3
              import os
              import sys
              sys.path.insert(0, "${patchScriptsDir}")
              from clang_ubsan_feature import rewrite_args
              compiler = "${pkgs.llvmPackages.clang}/bin/$tool"
              args = rewrite_args(sys.argv[1:], ignore_flag_supported=False)
              os.execv(compiler, [compiler, *args])
              PY
              chmod +x $llvm_bin/$tool
            done
          fi
        ''
        """,
    )


def test_rusty_v8_indexes_linux_archives_for_gnu_ld() -> None:
    """Clang without lld emits a thin archive ld.bfd cannot read. Goose and Codex share the wrapper."""
    assert_nix_ast_equal(
        nix_file_binding_expr("lib/rusty-v8.nix", "llvmArLink"),
        """
        if pkgs.stdenv.hostPlatform.isLinux then
          ''
            cat > $llvm_bin/llvm-ar <<PY
            #!${pkgs.python3}/bin/python3
            import os
            import sys
            llvm_ar = "${pkgs.llvmPackages.llvm}/bin/llvm-ar"
            args = ["-s" if arg == "-S" else arg for arg in sys.argv[1:]]
            os.execv(llvm_ar, [llvm_ar, *args])
            PY
            chmod +x $llvm_bin/llvm-ar
          ''
        else if pkgs.stdenv.hostPlatform.isDarwin then
          ''
            ln -sf ${pkgs.llvmPackages.llvm}/bin/llvm-ar $llvm_bin/llvm-ar
          ''
        else
          abort "rusty-v8 has no llvm-ar layout for this platform"
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
