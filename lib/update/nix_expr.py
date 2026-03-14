"""Helpers for building and normalizing generated Nix expressions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.select import Select

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


def compact_nix_expr(expr: str) -> str:
    """Collapse generated Nix code into a single-line expression."""
    return " ".join(line.strip() for line in expr.splitlines() if line.strip())


def select_attrs(expression: NixExpression, *attributes: str) -> NixExpression:
    """Select a dotted attribute path from an existing expression."""
    selected = expression
    for attribute in attributes:
        selected = Select(expression=selected, attribute=attribute)
    return selected


def identifier_attr_path(name: str, *attributes: str) -> NixExpression:
    """Build a dotted attribute path starting from an identifier."""
    return select_attrs(Identifier(name=name), *attributes)


__all__ = ["compact_nix_expr", "identifier_attr_path", "select_attrs"]
