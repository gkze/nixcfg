"""Structural contracts for the shared rusty-v8 builder."""

import ast
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import pytest
from nix_manipulator import parse

from lib.tests._nix_ast import (
    _normalize_indented_string_body,
    assert_nix_ast_equal,
    parse_nix_expr,
)
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
            cp ${ubsanClangWrapper "clang"} $llvm_bin/clang
            cp ${ubsanClangWrapper "clang++"} $llvm_bin/clang++
            chmod +x $llvm_bin/clang $llvm_bin/clang++
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


_SHELL_BINDINGS = frozenset({"clangLink", "llvmArLink", "clangRuntimeLink"})
_PLACEHOLDER = "/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-placeholder"
_HEREDOC_TERMINATOR = re.compile(
    r"<<(?P<dash>-)?\s*(?P<quote>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)\s*$"
)


def _node_text(node: Node, encoded: bytes) -> str:
    return encoded[node.start_byte : node.end_byte].decode()


def _builder_bindings() -> tuple[dict[str, Node], bytes]:
    source = (REPO_ROOT / "lib/rusty-v8.nix").read_text(encoding="utf-8")
    encoded = source.encode()
    found: dict[str, Node] = {}

    def visit(node: Node) -> None:
        if node.type == "binding":
            children = node.named_children
            if len(children) >= 2 and children[0].type == "attrpath":
                name = _node_text(children[0], encoded)
                if name in {
                    *_SHELL_BINDINGS,
                    "chromiumToolchainBundle",
                    "ubsanClangWrapper",
                }:
                    found[name] = children[1]
        for child in node.named_children:
            visit(child)

    visit(parse(source).node)
    return found, encoded


def _only_indented_string(node: Node) -> Node:
    found: list[Node] = []

    def visit(current: Node) -> None:
        if current.type == "indented_string_expression":
            found.append(current)
            return
        for child in current.named_children:
            visit(child)

    visit(node)
    assert len(found) == 1
    return found[0]


def _render_indented(
    node: Node,
    encoded: bytes,
    resolve: Callable[[str], str],
) -> str:
    parts: list[str] = []
    interpolations: list[str] = []
    for child in node.children:
        if child.type == "''":
            continue
        if child.type == "string_fragment":
            parts.append(_node_text(child, encoded))
            continue
        if child.type == "interpolation":
            expression = child.named_children[0]
            marker = f"\0{len(interpolations)}\0"
            interpolations.append(_node_text(expression, encoded))
            parts.append(marker)
            continue
        raise AssertionError(child.type)

    rendered = _normalize_indented_string_body("".join(parts))
    for index, expression in enumerate(interpolations):
        rendered = rendered.replace(f"\0{index}\0", resolve(expression), 1)
    return rendered


def _render_shell(
    node: Node,
    encoded: bytes,
    platform: str,
    bindings: dict[str, Node],
) -> str:
    def resolve(expression: str) -> str:
        if expression in _SHELL_BINDINGS:
            return _render_shell(bindings[expression], encoded, platform, bindings)
        return _PLACEHOLDER

    if node.type == "if_expression":
        condition, consequence, alternative = node.named_children
        condition_text = _node_text(condition, encoded)
        if condition_text == "pkgs.stdenv.hostPlatform.isLinux":
            chosen = consequence if platform == "linux" else alternative
        elif condition_text == "pkgs.stdenv.hostPlatform.isDarwin":
            chosen = consequence if platform == "darwin" else alternative
        else:
            raise AssertionError(condition_text)
        return _render_shell(chosen, encoded, platform, bindings)
    if node.type == "indented_string_expression":
        return _render_indented(node, encoded, resolve)
    if node.type == "apply_expression":
        return _render_indented(_only_indented_string(node), encoded, resolve)
    raise AssertionError(node.type)


def _toolchain_builder(platform: str) -> str:
    bindings, encoded = _builder_bindings()
    return _render_shell(
        bindings["chromiumToolchainBundle"],
        encoded,
        platform,
        bindings,
    )


def _bash_syntax(script: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(  # noqa: S603 -- bash syntax check of the rendered builder
        [bash, "-n"],
        check=False,
        capture_output=True,
        input=script,
        text=True,
    )


def _assert_heredocs_close_at_column_zero(script: str) -> None:
    lines = script.splitlines()
    index = 0
    while index < len(lines):
        match = _HEREDOC_TERMINATOR.search(lines[index])
        if match is None:
            index += 1
            continue
        assert match.group("dash") is None
        terminator = match.group("word")
        index += 1
        while index < len(lines) and lines[index] != terminator:
            index += 1
        assert index < len(lines), f"heredoc {terminator} never reaches column zero"
        index += 1


def test_bash_n_rejects_a_heredoc_terminator_that_dedent_leaves_indented() -> None:
    """A terminator nested under if/for stays indented, so bash never sees `done`."""
    script = _normalize_indented_string_body(
        "\n"
        "  if true; then\n"
        "    for tool in clang clang++; do\n"
        "      cat > $tool <<PY\n"
        "      echo hi\n"
        "      PY\n"
        "      chmod +x $tool\n"
        "    done\n"
        "  fi\n"
    )
    assert "    PY" in script.splitlines()
    assert "PY" not in script.splitlines()
    with pytest.raises(AssertionError, match="PY"):
        _assert_heredocs_close_at_column_zero(script)
    result = _bash_syntax(script)
    assert result.returncode != 0
    assert "PY" in result.stderr
    assert "end of file" in result.stderr


def test_bash_n_accepts_a_heredoc_whose_terminator_dedents_to_column_zero() -> None:
    """A uniformly indented heredoc is the shape the Linux llvm-ar wrapper uses."""
    script = _normalize_indented_string_body("\n  cat > $tool <<PY\n  echo hi\n  PY\n")
    assert "\nPY\n" in script
    result = _bash_syntax(script)
    assert result.returncode == 0, result.stderr


def test_ubsan_clang_wrapper_stays_trap_only_python() -> None:
    """The wrapper still clears the feature so testing.cc does not include the sanitizer runtime."""
    bindings, encoded = _builder_bindings()
    script = _render_indented(
        _only_indented_string(bindings["ubsanClangWrapper"]),
        encoded,
        lambda _expression: "placeholder",
    )
    shebang, body = script.split("\n", 1)
    assert shebang.startswith("#!")
    assert shebang == shebang.lstrip(" ")
    tree = ast.parse(body)
    rewrites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "rewrite_args"
    ]
    assert len(rewrites) == 1
    keywords = {keyword.arg: keyword.value for keyword in rewrites[0].keywords}
    ignore_flag = keywords["ignore_flag_supported"]
    assert isinstance(ignore_flag, ast.Constant)
    assert ignore_flag.value is False
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execv"
        for node in ast.walk(tree)
    )


def test_rusty_v8_toolchain_builder_parses_as_bash() -> None:
    """Gate 45785077844 failed this runCommand because its heredoc swallowed `done`."""
    for platform in ("linux", "darwin"):
        script = _toolchain_builder(platform)
        _assert_heredocs_close_at_column_zero(script)
        result = _bash_syntax(script)
        assert result.returncode == 0, (platform, result.stderr)
        assert f"cp {_PLACEHOLDER} $llvm_bin/clang\n" in script
        assert f"cp {_PLACEHOLDER} $llvm_bin/clang++\n" in script
        assert "-fsanitize-ignore-for-ubsan-feature=return" in script
        assert "for tool in clang" not in script
        if platform == "linux":
            assert script.count("<<") == 1
        else:
            assert "<<" not in script
