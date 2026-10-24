"""Pin Nix ``builtins.tryEval`` catchability assumed by validation bisection.

Why real Nix evaluation: this contract is a property of the evaluator binary
itself, not of any Python decision logic, so it cannot be established at the
AST level or through the mocked subprocess boundary. The commands are tiny
``--expr`` evaluations; nothing is built, imported from a derivation, or read
from this repository's flake.

:mod:`lib.update.derivation_validation` isolates failing targets inside a
batched eval by subdividing the batch, because one Nix command cannot report
which of its requests failed. That strategy is only correct while evaluator
errors such as missing attributes abort the whole eval instead of being
catchable in-expression: if a Nix change makes them catchable through
``builtins.tryEval``, single-command attribution becomes possible and the
bisection cost model (and its pinned command counts) changes. These tests
make that transition announce itself instead of silently changing how much
isolation work a failing validation group costs.
"""

import subprocess

import pytest

from lib.tests._nix_ast import parse_nix_expr
from lib.tests._nix_eval import nix_eval_result


@pytest.mark.nix_eval(reason="Only the evaluator can pin tryEval catchability.")
def test_try_eval_catches_thrown_errors() -> None:
    """Thrown errors stay catchable, so probe-style tryEval use keeps working."""
    result = nix_eval_result(
        parse_nix_expr(
            'let v = builtins.tryEval (throw "boom");'
            ' in if v.success then v.value else "caught"'
        ),
        raw=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "caught"


@pytest.mark.nix_eval(
    reason="Only the evaluator can pin missing-attribute attribution."
)
def test_missing_attribute_errors_escape_try_eval() -> None:
    """Missing attributes abort the whole eval.

    Batch attribution therefore cannot be delegated to the evaluator, and
    batch subdivision remains required.
    """
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_result(
            parse_nix_expr(
                'let v = builtins.tryEval ({ a = "1"; }.b);'
                ' in if v.success then v.value else "caught"'
            ),
            raw=True,
        )
    assert "attribute 'b' missing" in exc_info.value.stderr
