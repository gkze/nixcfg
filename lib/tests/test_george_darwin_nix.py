"""Regression checks for George's Darwin Home Manager hooks."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell
from lib.update.paths import REPO_ROOT


@cache
def _darwin_module_output() -> AttributeSet:
    expr = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "home/george/darwin.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


def test_darwin_home_activation_repairs_opencode_dev_dock_after_install_packages() -> (
    None
):
    """The Dock repair must run after Home Manager materializes app bundles."""
    home = expect_instance(
        expect_binding(_darwin_module_output().values, "home").value, AttributeSet
    )
    activation = expect_instance(
        expect_binding(home.values, "activation").value, AttributeSet
    )
    repair = expect_instance(
        expect_binding(activation.values, "repairTownDockOpenCodeDev").value,
        FunctionCall,
    )
    entry_after = expect_instance(repair.name, FunctionCall)

    assert_nix_ast_equal(entry_after.name, "lib.hm.dag.entryAfter")
    assert_nix_ast_equal(entry_after.argument, '[ "installPackages" ]')

    script = expect_instance(repair.argument, IndentedString)
    shell = parse_shell(indented_string_body(script.rebuild()))

    dockutil_commands = [
        text
        for text in command_texts(shell)
        if text.startswith("__NIX_INTERP__/bin/dockutil")
    ]
    assert any(
        '--remove "OpenCode Electron Dev" --no-restart' in text
        for text in dockutil_commands
    )
    assert any(
        '--remove "OpenCode Dev" --no-restart' in text for text in dockutil_commands
    )
    assert any(
        '--add __NIX_INTERP__ --after "Claude"' in text for text in dockutil_commands
    )
    assert any(text == "exit 0" for text in command_texts(shell, "exit"))
