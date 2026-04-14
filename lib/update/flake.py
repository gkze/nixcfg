"""flake.lock lookup helpers and flake input update streaming."""

from __future__ import annotations

import functools
import os
import shlex
from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.path import NixPath
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.commands.flake import nix_flake_lock_update
from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import FLAKE_LOCK_FILE

if TYPE_CHECKING:
    from nix_manipulator.expressions.expression import NixExpression


@functools.cache
def load_flake_lock() -> FlakeLock:
    """Load and cache the flake.lock as a validated :class:`FlakeLock` model."""
    return FlakeLock.from_file(FLAKE_LOCK_FILE)


def invalidate_flake_lock() -> None:
    """Clear the cached flake.lock model after on-disk updates."""
    load_flake_lock.cache_clear()


def get_flake_input_node(input_name: str) -> FlakeLockNode:
    """Look up a node by name in the flake.lock."""
    lock = load_flake_lock()
    node = lock.nodes.get(input_name)
    if node is None:
        msg = f"flake input '{input_name}' not found in flake.lock"
        raise KeyError(msg)
    return node


def get_root_input_name(input_name: str) -> str:
    """Follow the root node's input indirection to resolve the actual node name."""
    lock = load_flake_lock()
    root = lock.root_node
    if root.inputs:
        target = root.inputs.get(input_name, input_name)
        return target if isinstance(target, str) else input_name
    return input_name


def resolve_root_input_node(
    lock: FlakeLock,
    input_name: str,
) -> tuple[FlakeLockNode | None, str | None]:
    """Resolve a root input to its final node and optional follows path."""
    root_inputs = lock.root_node.inputs or {}
    target = root_inputs.get(input_name)
    follows = "/".join(target) if isinstance(target, list) else None
    if target is None:
        return None, follows
    if isinstance(target, str):
        return lock.nodes.get(target), follows

    node_name: str | None = None
    for i, segment in enumerate(target):
        if i == 0:
            node_name = segment
            continue
        if node_name is None:
            return None, follows
        node = lock.nodes.get(node_name)
        if node is None or node.inputs is None:
            return None, follows
        ref = node.inputs.get(segment)
        if not isinstance(ref, str):
            return None, follows
        node_name = ref

    if node_name is None:
        return None, follows
    return lock.nodes.get(node_name), follows


def get_flake_input_version(node: FlakeLockNode) -> str:
    """Extract a version string from a flake lock node."""
    if node.original:
        if node.original.ref:
            return node.original.ref
        rev = getattr(node.original, "rev", None)
        if rev:
            return rev
    if node.locked:
        return node.locked.rev or "unknown"
    return "unknown"


def flake_fetch_expression(node: FlakeLockNode) -> NixExpression:
    """Build a ``builtins.fetchTree`` expression from a locked flake node."""
    locked = node.locked
    if locked is None:
        msg = "Node has no locked ref"
        raise ValueError(msg)
    if locked.type not in {"github", "gitlab"}:
        msg = f"Unsupported flake input type: {locked.type}"
        raise ValueError(msg)
    if not locked.owner or not locked.repo or not locked.rev:
        msg = f"Incomplete locked ref for {locked.type}: missing owner/repo/rev"
        raise ValueError(msg)

    return FunctionCall(
        name=identifier_attr_path("builtins", "fetchTree"),
        argument=AttributeSet.from_dict(
            {
                "type": locked.type,
                "owner": locked.owner,
                "repo": locked.repo,
                "rev": locked.rev,
                "narHash": locked.nar_hash,
            },
        ),
    )


def flake_fetch_expr(node: FlakeLockNode) -> str:
    """Build a ``builtins.fetchTree`` expression from a locked flake node."""
    return flake_fetch_expression(node).rebuild()


def nixpkgs_expression() -> NixExpression:
    """Build a nixpkgs import expression from a local path or pinned flake input."""
    nixpkgs_path = os.environ.get("NIXCFG_NIXPKGS_PATH", "").strip()
    fetch_tree: NixExpression = (
        NixPath(path=nixpkgs_path)
        if nixpkgs_path
        else flake_fetch_expression(
            get_flake_input_node(get_root_input_name("nixpkgs"))
        )
    )
    import_fetch_tree = FunctionCall(
        name=Identifier(name="import"),
        argument=(
            fetch_tree
            if isinstance(fetch_tree, NixPath)
            else Parenthesis(value=fetch_tree)
        ),
    )
    return FunctionCall(
        name=import_fetch_tree,
        argument=AttributeSet.from_dict(
            {"system": identifier_attr_path("builtins", "currentSystem")},
        ),
    )


def nixpkgs_expr() -> str:
    """Build a nixpkgs import expression from the pinned flake input."""
    return compact_nix_expr(nixpkgs_expression().rebuild())


async def update_flake_input(input_name: str, *, source: str) -> EventStream:
    """Update a single flake input via :func:`lib.nix.commands.flake.nix_flake_lock_update`."""
    args = ["nix", "flake", "lock", "--update-input", input_name]
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=shlex.join(args),
        payload=args,
    )
    await nix_flake_lock_update(input_name)
    invalidate_flake_lock()
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_END,
        payload=CommandResult(args=args, returncode=0, stdout="", stderr=""),
    )
