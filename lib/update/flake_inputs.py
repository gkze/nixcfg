"""Locate and edit root input declarations in ``flake.nix`` source.

This module is the single owner of "where is input ``X`` declared". Both the
input-receipt scope (:mod:`lib.update.input_state`) and ref rewrites
(:mod:`lib.update.refs`) read declarations through the tree-sitter syntax tree
that nix-manipulator parses with.

Edits splice only the byte range of the node they replace and render new
values with nix-manipulator, so every untouched byte (comments, alignment,
blank lines) survives a rewrite. The whole-file ``rebuild()`` path is not used
because the repository flake's outputs contain syntax that nix-manipulator
keeps as an opaque raw expression.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nix_manipulator.parser import parse_to_ast

from lib.update.nix_expr import nix_str

if TYPE_CHECKING:
    from tree_sitter import Node

_INPUTS = b"inputs"


def _attrpath_parts(binding: Node) -> tuple[bytes, ...] | None:
    """Return a binding's static attribute path, or ``None`` when dynamic."""
    path = binding.child_by_field_name("attrpath")
    if path is None or path.has_error:
        return None
    parts = path.named_children
    if any(part.type != "identifier" or part.text is None for part in parts):
        return None
    return tuple(part.text for part in parts if part.text is not None)


def _bindings(attrset: Node) -> list[Node]:
    """Return the direct ``binding`` children of an attribute-set node."""
    return [
        binding
        for binding_set in attrset.named_children
        if binding_set.type == "binding_set"
        for binding in binding_set.named_children
        if binding.type == "binding"
    ]


def root_attrset(source: bytes) -> Node:
    """Return the flake's literal root attribute set node."""
    syntax = parse_to_ast(source)
    root = syntax.child_by_field_name("expression")
    if (
        root is None
        or root.type != "attrset_expression"
        or root.children[-1].is_missing
        or any(
            child != root and child.type != "comment" for child in syntax.named_children
        )
    ):
        msg = "Flake declarations require a literal root attribute set"
        raise ValueError(msg)
    return root


def root_input_bindings(source: bytes) -> list[Node]:
    """Return the root bindings whose attribute path starts with ``inputs``."""
    root = root_attrset(source)
    result = []
    for child in root.named_children:
        if child.type == "comment":
            continue
        if child.type != "binding_set":
            msg = "Unknown root declaration syntax"
            raise ValueError(msg)
        for binding in child.named_children:
            if binding.type == "comment":
                continue
            parts = _attrpath_parts(binding)
            if parts is None:
                msg = "Root attribute name is ambiguous"
                raise ValueError(msg)
            if parts[0] == _INPUTS:
                if binding.has_error:
                    msg = "Invalid input declaration"
                    raise ValueError(msg)
                result.append(binding)
    return result


def _input_attrset(source: bytes, input_name: str) -> Node | None:
    """Return the attribute set declaring ``input_name``, if there is one.

    Recognizes ``inputs = { <name> = { ... }; }`` and
    ``inputs.<name> = { ... };``. Other spellings (``inputs.<name>.url = ...``)
    have no attribute set to edit and yield ``None``.
    """
    name = input_name.encode()
    found: list[Node] = []
    for binding in root_input_bindings(source):
        parts = _attrpath_parts(binding)
        value = binding.child_by_field_name("expression")
        if value is None or value.type != "attrset_expression":
            continue
        if parts == (_INPUTS, name):
            found.append(value)
        elif parts == (_INPUTS,):
            found.extend(
                inner_value
                for inner in _bindings(value)
                if _attrpath_parts(inner) == (name,)
                and (inner_value := inner.child_by_field_name("expression")) is not None
                and inner_value.type == "attrset_expression"
            )
    if len(found) > 1:
        msg = f"Flake input {input_name!r} is declared more than once"
        raise ValueError(msg)
    return found[0] if found else None


@dataclass(frozen=True)
class _Splice:
    start: int
    end: int
    replacement: bytes

    def apply(self, source: bytes) -> bytes:
        return source[: self.start] + self.replacement + source[self.end :]


def _line_indent(source: bytes, offset: int) -> bytes | None:
    """Return the whitespace before ``offset`` on its line, or ``None``."""
    line_start = source.rfind(b"\n", 0, offset) + 1
    prefix = source[line_start:offset]
    return prefix if prefix.strip() == b"" else None


def _ref_splice(source: bytes, attrset: Node, ref: str) -> _Splice:
    rendered = nix_str(ref).rebuild().encode()
    bindings = _bindings(attrset)
    for binding in bindings:
        if _attrpath_parts(binding) == (b"ref",):
            value = binding.child_by_field_name("expression")
            if value is None:  # pragma: no cover -- tree-sitter always fills it
                break
            return _Splice(value.start_byte, value.end_byte, rendered)
    if not bindings:
        close = attrset.children[-1].start_byte
        return _Splice(close, close, b"ref = " + rendered + b"; ")
    last = bindings[-1]
    indent = _line_indent(source, bindings[0].start_byte)
    separator = b" " if indent is None else b"\n" + indent
    return _Splice(
        last.end_byte, last.end_byte, separator + b"ref = " + rendered + b";"
    )


def has_input_ref(source: str, input_name: str) -> bool:
    """Return whether ``input_name`` is declared as an attrset with a ``ref``."""
    attrset = _input_attrset(source.encode(), input_name)
    return attrset is not None and any(
        _attrpath_parts(binding) == (b"ref",) for binding in _bindings(attrset)
    )


def set_input_ref(source: str, input_name: str, ref: str) -> str:
    """Set (or add) the ``ref`` of an attrset-declared flake input.

    Only the ref string (or one inserted binding) changes; all other text is
    preserved byte for byte.
    """
    encoded = source.encode()
    attrset = _input_attrset(encoded, input_name)
    if attrset is None:
        msg = f"Could not find flake input attrset for {input_name!r}"
        raise RuntimeError(msg)
    return _ref_splice(encoded, attrset, ref).apply(encoded).decode()


__all__ = [
    "has_input_ref",
    "root_attrset",
    "root_input_bindings",
    "set_input_ref",
]
