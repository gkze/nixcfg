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


def test_darwin_home_activation_repairs_codex_bundled_plugins_after_write_boundary() -> (
    None
):
    """The Codex repair should run after Home Manager writes managed files."""
    home = expect_instance(
        expect_binding(_darwin_module_output().values, "home").value, AttributeSet
    )
    activation = expect_instance(
        expect_binding(home.values, "activation").value, AttributeSet
    )
    repair = expect_instance(
        expect_binding(activation.values, "codexBundledPluginRepair").value,
        FunctionCall,
    )
    entry_after = expect_instance(repair.name, FunctionCall)

    assert_nix_ast_equal(entry_after.name, "lib.hm.dag.entryAfter")
    assert_nix_ast_equal(entry_after.argument, '[ "writeBoundary" ]')

    script = expect_instance(repair.argument, IndentedString)
    shell = parse_shell(indented_string_body(script.rebuild()))
    assert "__NIX_INTERP__" in command_texts(shell)


def test_darwin_launchd_reruns_codex_bundled_plugin_repair() -> None:
    """The Codex repair should keep running after activation for app self-updates."""
    launchd = expect_instance(
        expect_binding(_darwin_module_output().values, "launchd").value,
        AttributeSet,
    )
    agents = expect_instance(
        expect_binding(launchd.values, "agents").value, AttributeSet
    )
    agent = expect_instance(
        expect_binding(agents.values, "codex-bundled-plugin-repair").value,
        AttributeSet,
    )
    config = expect_instance(expect_binding(agent.values, "config").value, AttributeSet)

    assert_nix_ast_equal(expect_binding(agent.values, "enable").value, "true")
    assert_nix_ast_equal(
        expect_binding(config.values, "Label").value,
        '"dev.george.codex-bundled-plugin-repair"',
    )
    assert_nix_ast_equal(expect_binding(config.values, "RunAtLoad").value, "true")
    assert_nix_ast_equal(expect_binding(config.values, "StartInterval").value, "30")
    assert_nix_ast_equal(
        expect_binding(config.values, "ProgramArguments").value,
        "[ (lib.getExe codexBundledPluginRepair) ]",
    )
