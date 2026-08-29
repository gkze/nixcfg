"""Contracts for the Axiom CLI package."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.update.paths import REPO_ROOT


def test_axiom_cli_selects_upstream_required_go_toolchain() -> None:
    """Build Axiom with the newest Go toolchain available in the pinned nixpkgs."""
    function = expect_instance(
        nix_file_expr(REPO_ROOT / "packages/axiom-cli/default.nix"),
        FunctionDefinition,
    )
    call = expect_instance(function.output, FunctionCall)
    arguments = expect_instance(call.argument, AttributeSet)

    assert_nix_ast_equal(expect_binding(arguments.values, "go").value, "go_1_27")


def test_axiom_cli_excludes_release_tooling_from_runtime_vendoring() -> None:
    """Developer tools must not raise the CLI runtime's Go version floor."""
    function = expect_instance(
        nix_file_expr(REPO_ROOT / "packages/axiom-cli/default.nix"),
        FunctionDefinition,
    )
    call = expect_instance(function.output, FunctionCall)
    arguments = expect_instance(call.argument, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(arguments.values, "postPatch").value,
        """
        ''
          go mod edit \\
            -droptool=github.com/axiomhq/cli/tools/gen-cli-docs \\
            -droptool=github.com/axiomhq/cli/tools/loggen \\
            -droptool=github.com/golangci/golangci-lint/v2/cmd/golangci-lint \\
            -droptool=github.com/goreleaser/goreleaser/v2 \\
            -droptool=golang.org/x/tools/cmd/stringer \\
            -droptool=gotest.tools/gotestsum
        ''
        """,
    )
