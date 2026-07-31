"""Structural tests for the shared T3 Code workspace build."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.tests._shell_ast import command_texts, indented_string_body, parse_shell


def test_t3code_workspace_pnpm_fetch_is_resilient() -> None:
    """Large workspace dependency fetches should tolerate registry instability."""
    package = expect_instance(
        nix_file_expr("packages/t3code/_shared.nix"),
        FunctionDefinition,
    )
    node_modules = expect_instance(
        expect_binding(package.output.scope, "node_modules").value,
        IfExpression,
    )
    args = expect_instance(
        expect_binding(node_modules.scope, "args").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(args.values, "pnpmInstallFlags").value,
        """[
          "--fetch-retries=5"
          "--network-concurrency=1"
        ]""",
    )


def test_t3code_workspace_uses_nix_pnpm_without_global_config() -> None:
    """Pnpm 11 should use the Nix-pinned executable without mutating global config."""
    package = expect_instance(
        nix_file_expr("packages/t3code/_shared.nix"),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        expect_binding(package.output.scope, "pnpm").value,
        "pnpm_11.override { nodejs-slim = nodejs; }",
    )
    workspace_build = expect_instance(
        expect_binding(package.output.scope, "workspaceBuild").value,
        FunctionCall,
    )
    derivation = expect_instance(workspace_build.argument, AttributeSet)
    environment = expect_instance(
        expect_binding(derivation.values, "env").value,
        AttributeSet,
    )
    build_phase = expect_instance(
        expect_binding(derivation.values, "buildPhase").value,
        IndentedString,
    )
    build_shell = parse_shell(indented_string_body(build_phase.rebuild()))

    assert_nix_ast_equal(
        expect_binding(
            environment.values,
            "pnpm_config_pm_on_fail",
        ).value,
        StringPrimitive(value="ignore"),
    )
    assert command_texts(build_shell, "pnpm") == ["pnpm run build:desktop"]
