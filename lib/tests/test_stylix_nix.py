"""Semantic checks for evaluator-visible Stylix theme inputs."""

import subprocess
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_eval import nix_attrset, nix_eval_raw, nix_import, nix_let
from lib.tests._nix_source import nix_file_expr
from lib.update.flake import nixpkgs_expression
from lib.update.nix_expr import identifier_attr_path
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


def _stylix_harness_expression(**arguments: object) -> NixExpression:
    return nix_let(
        {"nixpkgs": nixpkgs_expression()},
        FunctionCall(
            name=nix_import(REPO_ROOT / "tests/nix/stylix-explicit-scheme.nix"),
            argument=nix_attrset({
                "lib": identifier_attr_path("nixpkgs", "lib"),
                **arguments,
            }),
        ),
    )


def test_default_base16_scheme_pin_tracks_nixpkgs_source_metadata() -> None:
    """A nixpkgs bump must fail loudly when the evaluator-visible source pin drifts."""
    module = expect_instance(
        nix_file_expr("modules/home/stylix.nix"),
        FunctionDefinition,
    )
    source_check = expect_binding(
        module.output.scope,
        "base16SchemesSourceCheck",
    ).value

    assert_nix_ast_equal(
        f"let inputs = null; pkgs = null; in {source_check.rebuild()}",
        """let inputs = null; pkgs = null; in
        let
          inputSource = inputs.base16-schemes-src;
          nixpkgsSource = pkgs.base16-schemes.src;
        in
        if
          inputSource.rev == nixpkgsSource.rev
          && inputSource.narHash == nixpkgsSource.outputHash
        then
          true
        else
          throw ''
            base16-schemes-src is out of sync with pkgs.base16-schemes.src.
            Flake input: ${inputSource.rev} (${inputSource.narHash})
            Nixpkgs source: ${nixpkgsSource.rev} (${nixpkgsSource.outputHash})
            Update the base16-schemes-src pin in flake.nix and flake.lock to match nixpkgs.
          ''
        """,
    )


def test_explicit_base16_scheme_bypasses_default_source_metadata_check() -> None:
    """AST checks cannot prove the unused default metadata check remains lazy."""
    assert nix_eval_raw(_stylix_harness_expression()) == "/etc/hosts"


def test_default_base16_scheme_uses_evaluator_visible_flake_input() -> None:
    """AST checks cannot prove the matching default resolves during evaluation."""
    assert (
        nix_eval_raw(
            _stylix_harness_expression(
                explicitScheme=None,
                metadataMatches=True,
            )
        )
        == "/base16-input/base16/test-theme.yaml"
    )


def test_default_base16_scheme_rejects_mismatched_source_metadata() -> None:
    """AST checks cannot prove the default branch rejects mismatched metadata."""
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_raw(_stylix_harness_expression(explicitScheme=None))

    assert "base16-schemes-src is out of sync" in exc_info.value.stderr
