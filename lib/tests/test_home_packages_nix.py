"""AST-level regression checks for home package-set platform guards."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.with_statement import WithStatement

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _packages_module_output() -> AttributeSet:
    """Parse ``modules/home/packages.nix`` and return its module attrset."""
    expr = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "modules/home/packages.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(expr.output, AttributeSet)


@cache
def _package_set_table() -> NixList:
    """Return the package-set table body from the module scope."""
    package_sets = expect_instance(
        expect_scope_binding(_packages_module_output(), "packageSetTable").value,
        WithStatement,
    )
    return expect_instance(package_sets.body, NixList)


def _package_set_entry(name: str) -> AttributeSet:
    """Return one package-set entry by its declared ``name``."""
    for item in _package_set_table().value:
        entry = expect_instance(item, AttributeSet)
        entry_name = expect_instance(
            expect_binding(entry.values, "name").value,
            StringPrimitive,
        )
        if entry_name.value == name:
            return entry
    message = f"missing package set {name}"
    raise AssertionError(message)


def test_heavy_optional_goose_cli_is_guarded_to_aarch64_darwin() -> None:
    """Keep ``goose-cli`` out of unsupported Home Manager package-set evals."""
    assert_nix_ast_equal(
        expect_binding(_package_set_entry("heavyOptional").values, "packages").value,
        """
        lib.optionals (stdenv.isDarwin && stdenv.hostPlatform.isAarch64) [ goose-cli ]
        ++ [
          lumen
          czkawka
          mux
          scratch
          superset
        ]
        """,
    )
