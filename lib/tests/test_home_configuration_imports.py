"""Regression checks for George's home-manager import wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
from lib.update.paths import REPO_ROOT


@cache
def _configuration_output() -> AttributeSet:
    """Parse George's home configuration module and return its output attrset."""
    expr = expect_instance(
        parse_nix_expr(
            Path(REPO_ROOT / "home/george/configuration.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


def test_george_home_configuration_imports_canonical_exported_modules() -> None:
    """George's home config should consume the canonical exported home modules."""
    actual = expect_binding(_configuration_output().values, "imports").value

    assert_nix_ast_equal(
        actual,
        """
[
  {
    darwin = ./darwin.nix;
    linux = ./nixos.nix;
  }
  .${slib.kernel system}
  outputs.homeModules.nixcfgLanguageBun
  outputs.homeModules.nixcfgGit
  outputs.homeModules.nixcfgLanguageGo
  ./nixvim.nix
  outputs.homeModules.nixcfgOpencode
  outputs.homeModules.nixcfgPackages
  outputs.homeModules.nixcfgZen
  outputs.homeModules.nixcfgLanguagePython
  outputs.homeModules.nixcfgLanguageRust
  outputs.homeModules.nixcfgStylix
  outputs.homeModules.nixcfgZsh
  inputs.catppuccin.homeModules.catppuccin
]
""",
    )
