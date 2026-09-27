"""Identify the declarations and lock dependencies covered by an input refresh."""

import json
import re
from functools import lru_cache

from nix_manipulator import parse
from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.primitive import Primitive, StringPrimitive
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse_to_ast

from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode

type FlakeInputState = tuple[bytes, bytes]

_STATIC_ATTRIBUTE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_'\-]*")
_LOCK_VERSION = 7
_SCOPED_SOURCE_TYPES = frozenset({"git", "github", "gitlab", "tarball"})


@lru_cache(maxsize=4)
def _input_declarations(declarations: bytes) -> dict[str, object]:
    # Parse only input bindings into expression objects. The parser can reject
    # supported syntax inside outputs; those expressions cannot alter a literal
    # root attribute set's inputs and are outside this receipt's scope.
    syntax = parse_to_ast(declarations)
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
    input_bindings = []
    for binding_set in root.named_children:
        if binding_set.type == "comment":
            continue
        if binding_set.type != "binding_set":
            msg = "Unknown root declaration syntax"
            raise ValueError(msg)
        for binding in binding_set.named_children:
            if binding.type == "comment":
                continue
            path = binding.child_by_field_name("attrpath")
            if (
                path is None
                or path.has_error
                or any(part.type != "identifier" for part in path.named_children)
            ):
                msg = "Root attribute name is ambiguous"
                raise ValueError(msg)
            if path.named_children[0].text == b"inputs":
                if binding.has_error:
                    msg = "Invalid input declaration"
                    raise ValueError(msg)
                input_bindings.append(
                    declarations[binding.start_byte : binding.end_byte]
                )
    parsed = parse(b"{" + b"\n".join(input_bindings) + b"}")
    return _bindings(_bindings(parsed.expr)["inputs"])


def _bindings(expression: object) -> dict[str, object]:
    if (
        not isinstance(expression, AttributeSet)
        or expression.recursive
        or expression.has_scope()
    ):
        msg = "Input declarations require a literal attribute set"
        raise ValueError(msg)
    result = {}
    for binding in expression.values:
        if (
            not isinstance(binding, Binding)
            or _STATIC_ATTRIBUTE.fullmatch(binding.name) is None
            or binding.name in result
        ):
            msg = "Input declarations contain an ambiguous attribute"
            raise ValueError(msg)
        result[binding.name] = binding.value
    return result


def _literal(expression: object) -> object:
    if isinstance(expression, AttributeSet):
        return {name: _literal(value) for name, value in _bindings(expression).items()}
    if (
        not isinstance(expression, Primitive)
        or expression.has_scope()
        or (isinstance(expression, StringPrimitive) and "${" in expression.value)
    ):
        msg = "Input declaration may depend on another expression"
        raise ValueError(msg)
    return type(expression).__name__, expression.value


def _has_unknown_dependencies(node: FlakeLockNode) -> bool:
    # Unknown fetcher extensions can refer to a parent outside the inputs map.
    return bool(node.model_extra) or any(
        reference is not None
        and (reference.type not in _SCOPED_SOURCE_TYPES or bool(reference.model_extra))
        for reference in (node.locked, node.original)
    )


def _lock_dependencies(
    lock: FlakeLock, input_name: str
) -> tuple[dict[str, str | list[str]], dict[str, FlakeLockNode]]:
    """Keep reachable nodes and root mappings, including root-relative follows."""
    root_inputs: dict[str, str | list[str]] = {}
    nodes: dict[str, FlakeLockNode] = {}
    pending: list[str] = []

    def resolve(
        owner: str, name: str, resolving: frozenset[tuple[str, str]] = frozenset()
    ) -> str:
        edge = owner, name
        if edge in resolving:
            msg = "Cyclic follows path"
            raise ValueError(msg)
        target = (lock.nodes[owner].inputs or {})[name]
        if owner == lock.root:
            root_inputs[name] = target
        if isinstance(target, str):
            return target
        node_name = lock.root
        for segment in target:
            node_name = resolve(node_name, segment, resolving | {edge})
            # Intermediate mappings can change where a follows path leads.
            pending.append(node_name)
        return node_name

    pending.append(resolve(lock.root, input_name))
    while pending:
        node_name = pending.pop()
        if node_name == lock.root:
            msg = "Input follows the root flake"
            raise ValueError(msg)
        if node_name in nodes:
            continue
        node = lock.nodes[node_name]
        if node.locked is None or _has_unknown_dependencies(node):
            msg = "Unknown lock node fields may describe dependencies"
            raise ValueError(msg)
        nodes[node_name] = node
        pending.extend(resolve(node_name, name) for name in node.inputs or {})

    # A root declaration may change before its shared lock node is refreshed.
    root_inputs.update({
        name: target
        for name, target in (lock.root_node.inputs or {}).items()
        if isinstance(target, str) and target in nodes
    })
    return root_inputs, nodes


@lru_cache(maxsize=256)
def flake_input_state(
    input_name: str, declarations: bytes, lock_contents: bytes
) -> FlakeInputState:
    """Scope receipts to proven dependencies; otherwise retain exact file identity.

    Only literal declarations and the understood lock schema can prove that an
    unrelated input edit is independent. Dynamic Nix, missing nodes, and unknown
    graph semantics keep the conservative full-file receipt instead.
    """
    try:
        inputs = _input_declarations(declarations)
        lock = FlakeLock.model_validate_json(lock_contents, strict=True)
        if (
            lock.version != _LOCK_VERSION
            or "root" not in lock.model_fields_set
            or _has_unknown_dependencies(lock.root_node)
        ):
            return declarations, lock_contents
        root_inputs, nodes = _lock_dependencies(lock, input_name)
        input_declarations = {name: _literal(inputs[name]) for name in root_inputs}
        graph = {
            "root": lock.root,
            "version": lock.version,
            "rootNode": lock.root_node.model_dump(
                exclude={"inputs"}, exclude_unset=True
            ),
            "inputs": root_inputs,
            "nodes": {
                name: node.model_dump(exclude_unset=True)
                for name, node in nodes.items()
            },
        }
    except KeyError, ValueError:
        return declarations, lock_contents
    return (
        json.dumps(input_declarations, sort_keys=True).encode(),
        json.dumps(graph, sort_keys=True).encode(),
    )
