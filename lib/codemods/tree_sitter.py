"""Low-level Tree-sitter helpers for codemods that need exact CST control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree

from lib.codemods.errors import CodemodError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class ByteEdit:
    """A byte-range source edit."""

    start: int
    end: int
    replacement: bytes


@dataclass(frozen=True)
class TreeSitterMatch:
    """One Tree-sitter query match with grouped captures."""

    pattern_index: int
    captures: Mapping[str, Sequence[Node]]


def _source_bytes(source: str | bytes) -> bytes:
    return source.encode() if isinstance(source, str) else source


def parse_tree(
    source: str | bytes,
    *,
    language: Language,
    fail_on_error: bool = True,
) -> Tree:
    """Parse source with raw Tree-sitter bindings."""
    tree = Parser(language).parse(_source_bytes(source))
    if fail_on_error and tree.root_node.has_error:
        msg = "Tree-sitter parsed source with syntax errors"
        raise CodemodError(msg)
    return tree


def query_matches(
    source: str | bytes,
    *,
    language: Language,
    query: str,
    fail_on_error: bool = True,
) -> list[TreeSitterMatch]:
    """Run a Tree-sitter query and return grouped matches."""
    tree = parse_tree(source, language=language, fail_on_error=fail_on_error)
    compiled = Query(language, query)
    return [
        TreeSitterMatch(pattern_index=pattern_index, captures=captures)
        for pattern_index, captures in QueryCursor(compiled).matches(tree.root_node)
    ]


def require_capture(match: TreeSitterMatch, name: str, *, context: str) -> Node:
    """Return a required single Tree-sitter capture."""
    nodes = match.captures.get(name, ())
    if len(nodes) == 1:
        return nodes[0]
    msg = f"expected one Tree-sitter capture {name!r} in {context}, found {len(nodes)}"
    raise CodemodError(msg)


def apply_byte_edits(source: str | bytes, edits: Sequence[ByteEdit]) -> bytes:
    """Apply non-overlapping byte edits to source."""
    source_bytes = _source_bytes(source)
    source_length = len(source_bytes)
    sorted_edits = sorted(edits, key=lambda edit: edit.start)
    previous_end = 0
    chunks: list[bytes] = []
    for edit in sorted_edits:
        if edit.start < 0 or edit.end < 0 or edit.start > edit.end:
            msg = f"invalid byte edit range {edit.start}-{edit.end}"
            raise CodemodError(msg)
        if edit.start > source_length or edit.end > source_length:
            msg = (
                f"byte edit range {edit.start}-{edit.end} is outside source "
                f"length {source_length}"
            )
            raise CodemodError(msg)
        if edit.start < previous_end:
            msg = f"overlapping byte edit starts at {edit.start} before {previous_end}"
            raise CodemodError(msg)
        chunks.append(source_bytes[previous_end : edit.start])
        chunks.append(edit.replacement)
        previous_end = edit.end
    chunks.append(source_bytes[previous_end:])
    return b"".join(chunks)
