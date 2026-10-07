"""Codex desktop must reuse the updater's prefetched archive identity."""

from pathlib import Path

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, binding_map, parse_nix_expr


def test_codex_desktop_keeps_the_prefetched_source_name() -> None:
    """A custom fetch name would force a second download of a mutable URL."""
    package = expect_instance(
        parse_nix_expr(
            Path(__file__).with_name("default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    call = expect_instance(package.output, FunctionCall)
    assert_nix_ast_equal(call.name, Identifier(name="mkZipApp"))
    args = expect_instance(call.argument, AttributeSet)
    bindings = binding_map(args.values)
    assert "sourceName" not in bindings
    assert_nix_ast_equal(bindings["info"].value, Identifier(name="selfSource"))
