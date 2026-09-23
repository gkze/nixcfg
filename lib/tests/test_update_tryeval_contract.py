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

import shutil
import subprocess

import pytest

_EVAL_TIMEOUT = 60


def _eval(nix: str, expr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- argv from shutil.which, no shell
        [nix, "eval", "--impure", "--raw", "--expr", expr],
        capture_output=True,
        text=True,
        timeout=_EVAL_TIMEOUT,
        check=False,
    )


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix is not available")
def test_try_eval_catches_thrown_errors() -> None:
    """Thrown errors stay catchable, so probe-style tryEval use keeps working."""
    nix = shutil.which("nix")
    assert nix is not None
    result = _eval(
        nix,
        'let v = builtins.tryEval (throw "boom");'
        ' in if v.success then v.value else "caught"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "caught"


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix is not available")
def test_missing_attribute_errors_escape_try_eval() -> None:
    """Missing attributes abort the whole eval.

    Batch attribution therefore cannot be delegated to the evaluator, and
    batch subdivision remains required.
    """
    nix = shutil.which("nix")
    assert nix is not None
    result = _eval(
        nix,
        'let v = builtins.tryEval ({ a = "1"; }.b);'
        ' in if v.success then v.value else "caught"',
    )
    assert result.returncode != 0
    assert "attribute 'b' missing" in result.stderr
