"""Contracts for the OpenAI CLI package."""

from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_file_expr


def test_openai_cli_skips_upstream_checks_only_for_v1_38_0() -> None:
    """v1.38.0 checkPhase fails in upstream tests after the build succeeds."""
    package = expect_instance(
        nix_file_expr("packages/openai-cli/default.nix"),
        FunctionDefinition,
    )
    assert_nix_ast_equal(
        package.output,
        """
        (mkGoCli {
          pname = "openai-cli";
          cmdName = "openai";
          completionCommand = "@completion";
          description = "The official CLI for the OpenAI API";
          homepage = "https://github.com/openai/openai-cli";
          license = lib.licenses.asl20;
        }).overrideAttrs (old: {
          doCheck = old.version != "1.38.0";
        })
        """,
    )
