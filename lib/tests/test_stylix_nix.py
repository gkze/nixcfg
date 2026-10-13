"""Semantic checks for evaluator-visible Stylix theme inputs."""

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_eval import nix_attrset, nix_eval_raw, nix_import
from lib.tests._nix_source import nix_file_expr
from lib.update.flake import nixpkgs_lib_expression
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression

_PROBE_TIMEOUT_SECONDS = 120


def _raw_eval_command() -> list[str] | None:
    """Return the raw-value eval invocation, or None when Nix is unavailable."""
    nix_instantiate = shutil.which("nix-instantiate")
    if nix_instantiate is not None:
        return [nix_instantiate, "--eval", "--strict", "--raw"]
    nix = shutil.which("nix")
    if nix is None:
        return None
    return [nix, "eval", "--impure", "--raw"]


def _nixpkgs_lib_is_evaluable() -> bool:
    """Return whether the harness can resolve nixpkgs lib in this environment.

    Inside the Nix gate ``NIXCFG_NIXPKGS_PATH`` supplies nixpkgs as a store path
    (flake.nix). Outside it the harness falls back to ``builtins.fetchTree``,
    which needs a writable Nix fetcher cache or network access. Probe that
    prerequisite once, so a developer run reports a skip with this reason
    instead of failing as if the theme input itself regressed.
    """
    if os.environ.get("NIXCFG_NIXPKGS_PATH", "").strip():
        return True
    command = _raw_eval_command()
    if command is None:
        return False
    try:
        expression = f'builtins.seq ({nixpkgs_lib_expression().rebuild()}) "ok"'
        probe = subprocess.run(  # noqa: S603 -- fixed Nix executable, owned expression
            [*command, "--expr", expression],
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "ok"


requires_nixpkgs_lib = pytest.mark.skipif(
    not _nixpkgs_lib_is_evaluable(),
    reason=(
        "nixpkgs lib is not evaluable here: set NIXCFG_NIXPKGS_PATH (the Nix "
        "gate does) or provide a writable Nix fetcher cache for builtins.fetchTree"
    ),
)


def _stylix_harness_expression(**arguments: object) -> NixExpression:
    return FunctionCall(
        name=nix_import(REPO_ROOT / "tests/nix/stylix-explicit-scheme.nix"),
        argument=nix_attrset({
            "lib": nixpkgs_lib_expression(),
            **arguments,
        }),
    )


def test_stylix_harness_imports_nixpkgs_lib_only() -> None:
    """The laziness harness must not instantiate the nixpkgs package set."""
    assert_nix_ast_equal(
        _stylix_harness_expression(),
        FunctionCall(
            name=nix_import(REPO_ROOT / "tests/nix/stylix-explicit-scheme.nix"),
            argument=nix_attrset({"lib": nixpkgs_lib_expression()}),
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


@requires_nixpkgs_lib
@pytest.mark.nix_eval(reason="Only evaluation proves the unused default remains lazy.")
def test_explicit_base16_scheme_bypasses_default_source_metadata_check() -> None:
    """AST checks cannot prove the unused default metadata check remains lazy."""
    assert nix_eval_raw(_stylix_harness_expression()) == "/etc/hosts"


@requires_nixpkgs_lib
@pytest.mark.nix_eval(
    reason="Only evaluation resolves the default through the module system."
)
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


@requires_nixpkgs_lib
@pytest.mark.nix_eval(reason="Only evaluation reaches the throwing module branch.")
def test_default_base16_scheme_rejects_mismatched_source_metadata() -> None:
    """AST checks cannot prove the default branch rejects mismatched metadata."""
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        nix_eval_raw(_stylix_harness_expression(explicitScheme=None))

    assert "base16-schemes-src is out of sync" in exc_info.value.stderr
