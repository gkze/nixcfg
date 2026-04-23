"""Helpers for parsing shell bodies embedded in Nix strings."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache

import tree_sitter_bash
from tree_sitter import Language, Node, Parser, Tree


@dataclass(frozen=True)
class ParsedShell:
    """Parsed shell source plus its sanitized text."""

    source: str
    sanitized: str
    tree: Tree


@cache
def _bash_parser() -> Parser:
    return Parser(Language(tree_sitter_bash.language()))


def _sanitize_nix_interpolations(text: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("''${", index):
            open_length = 4
        elif text.startswith("${", index):
            open_length = 2
        else:
            parts.append(text[index])
            index += 1
            continue

        depth = 1
        cursor = index + open_length
        while cursor < len(text) and depth > 0:
            if text.startswith("''${", cursor):
                depth += 1
                cursor += 4
                continue
            if text.startswith("${", cursor):
                depth += 1
                cursor += 2
                continue
            if text[cursor] == "}":
                depth -= 1
            cursor += 1

        parts.append("__NIX_INTERP__")
        index = cursor
    return "".join(parts)


def indented_string_body(text: str) -> str:
    """Strip the surrounding Nix indented-string delimiters."""
    stripped = text.removeprefix("''\n").removeprefix("''")
    return stripped.removesuffix("''")


def parse_shell(text: str) -> ParsedShell:
    sanitized = _sanitize_nix_interpolations(text)
    tree = _bash_parser().parse(sanitized.encode("utf-8"))
    error_nodes = list(iter_nodes(tree.root_node, "ERROR"))
    assert not error_nodes, "expected parseable shell source"
    return ParsedShell(source=text, sanitized=sanitized, tree=tree)


def node_text(node: Node, text: str) -> str:
    source = text.encode("utf-8")
    return source[node.start_byte : node.end_byte].decode("utf-8")


def iter_nodes(node: Node, type_name: str | None = None) -> Iterator[Node]:
    if type_name is None or node.type == type_name:
        yield node
    for child in node.children:
        yield from iter_nodes(child, type_name)


def command_name(node: Node, text: str) -> str | None:
    if node.type == "command":
        for child in node.children:
            if child.type == "command_name":
                return node_text(child, text)
    if node.type == "declaration_command" and node.children:
        return node_text(node.children[0], text)
    return None


def command_texts(shell: ParsedShell, name: str | None = None) -> list[str]:
    root = shell.tree.root_node
    texts: list[str] = []
    for node in iter_nodes(root):
        if node.type not in {"command", "declaration_command", "test_command"}:
            continue
        if name is not None and command_name(node, shell.sanitized) != name:
            continue
        texts.append(node_text(node, shell.sanitized))
    return texts
