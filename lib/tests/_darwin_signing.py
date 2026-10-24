"""Structural helpers for `signAndVerifyBundle` calls in package shell strings."""

from typing import TYPE_CHECKING, TypeIs

from nix_manipulator import parse
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import parse_nix_expr
from lib.tests._shell_ast import iter_nodes

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression
    from nix_manipulator.expressions.indented_string import IndentedString


def indented_string_interpolations(value: IndentedString) -> list[NixExpression]:
    """Return each `${...}` expression of an indented string, in source order."""
    source = value.rebuild()
    parsed = parse(source)
    assert parsed.contains_error is False
    source_bytes = source.encode("utf-8")
    return [
        parse_nix_expr(source_bytes[node.start_byte + 2 : node.end_byte - 1].decode())
        for node in iter_nodes(parsed.node, "interpolation")
    ]


def is_sign_and_verify_bundle_call(expression: NixExpression) -> TypeIs[FunctionCall]:
    """Return whether one interpolated expression is a `signAndVerifyBundle` call."""
    return (
        isinstance(expression, FunctionCall)
        and isinstance(expression.name, Identifier)
        and expression.name.name == "signAndVerifyBundle"
    )


def sign_and_verify_bundle_arguments(value: IndentedString) -> list[AttributeSet]:
    """Return the argument set of every `signAndVerifyBundle { ... }` interpolation."""
    return [
        expect_instance(expression.argument, AttributeSet)
        for expression in indented_string_interpolations(value)
        if is_sign_and_verify_bundle_call(expression)
    ]
