"""Structural tests for the Goose Desktop package."""

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr


def test_goose_desktop_pnpm_fetch_is_resilient() -> None:
    """The large fixed-output dependency fetch should tolerate registry resets."""
    package = expect_instance(
        nix_file_expr("packages/goose-desktop/default.nix"),
        FunctionDefinition,
    )
    pnpm_deps = expect_instance(
        expect_binding(package.output.scope, "pnpmDeps").value,
        IfExpression,
    )
    args = expect_instance(
        expect_binding(pnpm_deps.scope, "args").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(args.values, "pnpmInstallFlags").value,
        """[
          "--fetch-retries=5"
          "--network-concurrency=8"
        ]""",
    )
