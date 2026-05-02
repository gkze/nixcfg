"""AST-level checks for Emdash packaging wrappers."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, parse_shell
from lib.update.paths import REPO_ROOT


@cache
def _derivation_args() -> AttributeSet:
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/emdash/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(
        expect_instance(root.output, FunctionCall).argument, AttributeSet
    )


def _install_branch_scripts() -> tuple[IndentedString, IndentedString]:
    install_phase = expect_instance(
        expect_binding(_derivation_args().values, "installPhase").value,
        IfExpression,
    )
    return (
        expect_instance(install_phase.consequence, IndentedString),
        expect_instance(install_phase.alternative, IndentedString),
    )


def _compact_shell(text: str) -> str:
    return " ".join(text.split())


def test_emdash_launchers_are_installed_from_repo_scripts() -> None:
    """The platform launchers should live as shell files, not inline heredocs."""
    darwin_install, linux_install = _install_branch_scripts()
    darwin_shell = parse_shell(darwin_install.value)
    linux_shell = parse_shell(linux_install.value)

    assert command_texts(darwin_shell, "install") == [
        'install -d "$out/Applications"',
        'install -d "$out/bin"',
        'install -m755 __NIX_INTERP__ "$out/bin/emdash"',
    ]
    assert command_texts(linux_shell, "install") == [
        'install -d "$out/share/emdash"',
        'install -d "$out/bin"',
        'install -m755 __NIX_INTERP__ "$out/bin/emdash"',
    ]
    assert "linux*-unpacked" in linux_install.value
    assert '"$out/share/emdash/linux-unpacked"' in linux_install.value
    expected_substitute = (
        'substituteInPlace "$out/bin/emdash" \\ '
        '--replace-fail "#!/usr/bin/env bash" "#!__NIX_INTERP__" \\ '
        '--replace-fail "@out@" "$out"'
    )
    assert [
        _compact_shell(text)
        for text in command_texts(darwin_shell, "substituteInPlace")
    ] == [expected_substitute]
    assert [
        _compact_shell(text) for text in command_texts(linux_shell, "substituteInPlace")
    ] == [expected_substitute]
