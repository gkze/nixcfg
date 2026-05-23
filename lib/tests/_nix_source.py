"""Helpers for parsing focused Nix source fragments in tests."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from lib.tests._nix_ast import parse_nix_expr
from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


def nix_source_fragment(
    relative_path: str,
    start_marker: str,
    end_marker: str,
    *,
    occurrence: int = 0,
    strip_trailing_semicolon: bool = True,
) -> str:
    """Return a dedented source fragment from a Nix file under the repository root."""
    source = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    start = -1
    for _ in range(occurrence + 1):
        start = source.index(start_marker, start + 1)
    start += len(start_marker)
    end = source.index(end_marker, start)
    fragment = textwrap.dedent(source[start:end]).rstrip()
    if strip_trailing_semicolon:
        fragment = fragment.removesuffix(";")
    return fragment


def nix_source_fragment_expr(
    relative_path: str,
    start_marker: str,
    end_marker: str,
    *,
    occurrence: int = 0,
) -> NixExpression:
    """Parse a focused Nix source fragment from a file under the repository root."""
    return parse_nix_expr(
        nix_source_fragment(
            relative_path,
            start_marker,
            end_marker,
            occurrence=occurrence,
        )
    )


def nix_file_expr(relative_path: str) -> NixExpression:
    """Parse a whole Nix file under the repository root."""
    return parse_nix_expr((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
