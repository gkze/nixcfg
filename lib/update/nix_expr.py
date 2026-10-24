"""Helpers for building and normalizing generated Nix expressions."""

from typing import TYPE_CHECKING

from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.primitive import StringPrimitive, _escape_nix_string
from nix_manipulator.expressions.select import Select

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


def compact_nix_expr(expr: str) -> str:
    """Collapse generated Nix code into a single-line expression."""
    return " ".join(line.strip() for line in expr.splitlines() if line.strip())


def nix_str(value: str) -> StringPrimitive:
    """Build a string literal for data, keeping any ``${`` as literal text.

    Plain ``StringPrimitive`` renders ``${`` as interpolation, so upstream data
    such as a version or URL could otherwise run as Nix code.
    """
    return StringPrimitive(
        value=_escape_nix_string(value, escape_interpolation=True), raw_string=True
    )


def select_attrs(expression: NixExpression, *attributes: str) -> NixExpression:
    """Select a dotted attribute path from an existing expression."""
    selected = expression
    for attribute in attributes:
        selected = Select(expression=selected, attribute=attribute)
    return selected


def identifier_attr_path(name: str, *attributes: str) -> NixExpression:
    """Build a dotted attribute path starting from an identifier."""
    return select_attrs(Identifier(name=name), *attributes)


__all__ = ["compact_nix_expr", "identifier_attr_path", "nix_str", "select_attrs"]
