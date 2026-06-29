"""Tests for raw Tree-sitter codemod helpers."""

from __future__ import annotations

import pytest
import tree_sitter_bash as tsbash
from tree_sitter import Language

from lib.codemods.errors import CodemodError
from lib.codemods.tree_sitter import (
    ByteEdit,
    apply_byte_edits,
    parse_tree,
    query_matches,
    require_capture,
)

_BASH = Language(tsbash.language())
_QUERY = """
(command
  name: (command_name (word) @command_name)
  argument: (word) @argument) @command
"""


def test_query_matches_groups_captures() -> None:
    """Raw Tree-sitter helper should expose grouped query captures."""
    matches = query_matches("echo hello\n", language=_BASH, query=_QUERY)

    assert len(matches) == 1
    assert require_capture(matches[0], "command_name", context="demo").text == b"echo"
    assert require_capture(matches[0], "argument", context="demo").text == b"hello"


def test_query_matches_rejects_parse_errors() -> None:
    """Tree-sitter backup helpers should fail on syntax errors by default."""
    with pytest.raises(CodemodError, match="syntax errors"):
        parse_tree("if then\n", language=_BASH)


def test_require_capture_rejects_missing_or_repeated_capture() -> None:
    """Capture helpers should enforce a single semantic target."""
    matches = query_matches("echo hello\n", language=_BASH, query=_QUERY)

    with pytest.raises(CodemodError, match="missing"):
        require_capture(matches[0], "missing", context="demo")


def test_apply_byte_edits_applies_sorted_non_overlapping_edits() -> None:
    """Byte edits should be applied after range validation."""
    assert (
        apply_byte_edits(
            b"alpha beta gamma",
            [
                ByteEdit(start=6, end=10, replacement=b"BETA"),
                ByteEdit(start=0, end=5, replacement=b"ALPHA"),
            ],
        )
        == b"ALPHA BETA gamma"
    )


def test_apply_byte_edits_rejects_invalid_ranges() -> None:
    """Overlapping and inverted ranges should fail loudly."""
    with pytest.raises(CodemodError, match="overlapping"):
        apply_byte_edits(
            "abcdef",
            [
                ByteEdit(start=0, end=3, replacement=b"abc"),
                ByteEdit(start=2, end=4, replacement=b"cd"),
            ],
        )

    with pytest.raises(CodemodError, match="invalid byte edit"):
        apply_byte_edits("abcdef", [ByteEdit(start=4, end=2, replacement=b"x")])

    with pytest.raises(CodemodError, match="invalid byte edit"):
        apply_byte_edits("abcdef", [ByteEdit(start=-1, end=1, replacement=b"x")])

    with pytest.raises(CodemodError, match="outside source length"):
        apply_byte_edits("abcdef", [ByteEdit(start=6, end=7, replacement=b"x")])
