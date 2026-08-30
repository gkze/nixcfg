"""Structural tests for the Goose CLI crate2nix package."""

from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_eval import nix_eval_json, nix_import
from lib.tests._nix_source import nix_file_expr
from lib.update.paths import REPO_ROOT


def test_goose_cli_restores_bitcoin_internals_rust_version_metadata() -> None:
    """BuildRustCrate must receive the MSRV that crate2nix omits."""
    package = expect_instance(
        nix_file_expr("overlays/goose-cli/default.nix"),
        FunctionDefinition,
    )
    rust_versions = expect_binding(
        package.output.scope,
        "bitcoinInternalsRustVersions",
    ).value
    override = expect_binding(
        package.output.scope,
        "bitcoinInternalsOverride",
    ).value

    assert_nix_ast_equal(
        rust_versions,
        """
        prev.lib.mapAttrs'
          (pinName: rustVersion:
            prev.lib.nameValuePair
              (prev.lib.removePrefix bitcoinInternalsRustVersionPinPrefix pinName)
              rustVersion)
          (prev.lib.filterAttrs
            (pinName: _value:
              prev.lib.hasPrefix bitcoinInternalsRustVersionPinPrefix pinName)
            selfSource.pins)
        """,
    )
    assert_nix_ast_equal(
        override,
        """
        attrs:
        {
          "rust-version" =
            bitcoinInternalsRustVersions.${attrs.version}
              or (throw "review bitcoin-internals ${attrs.version} rust-version metadata");
        }
        """,
    )


def test_goose_cli_reviews_every_bitcoin_internals_version() -> None:
    """Use Nix because cross-artifact graph coverage cannot be proven by one AST."""
    versions = nix_eval_json(
        nix_import(REPO_ROOT / "tests/nix/goose-cli-bitcoin-internals-versions.nix")
    )

    assert versions == ["0.5.0", "0.6.0"]
