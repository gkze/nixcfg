"""Contracts for the Anthropic CLI package."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.update.paths import REPO_ROOT


def test_anthropic_cli_selects_go_that_meets_upstreams_floor() -> None:
    """anthropic-cli 2.5.0 requires go >= 1.26.10 (Update run 41527009563).

    nixpkgs' default Go (1.26.9 then) builds with GOTOOLCHAIN=local, so the
    package must select go_1_27 instead of weakening upstream's go line.
    """
    function = expect_instance(
        nix_file_expr(REPO_ROOT / "packages/anthropic-cli/default.nix"),
        FunctionDefinition,
    )
    call = expect_instance(function.output, FunctionCall)
    arguments = expect_instance(call.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "go").value,
        "go_1_27",
    )
    assert "postPatch" not in binding_map(arguments.values)
