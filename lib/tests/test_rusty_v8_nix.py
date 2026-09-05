"""Structural contracts for the shared rusty-v8 builder."""

from collections.abc import Iterator
from typing import TYPE_CHECKING

from nix_manipulator import parse

from lib.update.paths import REPO_ROOT

if TYPE_CHECKING:
    from tree_sitter import Node


def _nodes(root: Node) -> Iterator[Node]:
    yield root
    for child in root.named_children:
        yield from _nodes(child)


def test_rusty_v8_derives_clang_resource_version_from_immutable_source() -> None:
    """Concrete callers provide source identity, not duplicated Clang metadata."""
    source = (REPO_ROOT / "lib/rusty-v8.nix").read_text(encoding="utf-8")
    encoded = source.encode()
    formals = [
        node
        for node in _nodes(parse(source).node)
        if node.type == "formal"
        and node.named_children
        and node.named_children[0].type == "identifier"
        and encoded[
            node.named_children[0].start_byte : node.named_children[0].end_byte
        ].decode()
        in {"clangResourceVersion", "rustyV8Src"}
    ]

    formal_names = [
        encoded[
            formal.named_children[0].start_byte : formal.named_children[0].end_byte
        ].decode()
        for formal in formals
    ]
    assert formal_names == ["rustyV8Src"]
    assert [child.type for child in formals[0].named_children] == ["identifier"]
