"""flake.lock lookup helpers and flake input update streaming."""

import functools
import shlex

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse

from lib.nix.commands.flake import nix_flake_lock_update
from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.nix_expr import compact_nix_expr
from lib.update.paths import FLAKE_LOCK_FILE


@functools.cache
def load_flake_lock() -> FlakeLock:
    """Load and cache the flake.lock as a validated :class:`FlakeLock` model."""
    return FlakeLock.from_file(FLAKE_LOCK_FILE)


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


def flake_fetch_expr(node: FlakeLockNode) -> str:
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

    fetch_tree = FunctionCall(
        name="builtins.fetchTree",
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
    return fetch_tree.rebuild()


def nixpkgs_expr() -> str:
    """Build a nixpkgs import expression from the pinned flake input."""
    node_name = get_root_input_name("nixpkgs")
    node = get_flake_input_node(node_name)
    fetch_tree = parse(flake_fetch_expr(node)).expr
    import_fetch_tree = FunctionCall(
        name="import",
        argument=Parenthesis(value=fetch_tree),
    )
    import_nixpkgs = FunctionCall(
        name=import_fetch_tree.rebuild(),
        argument=AttributeSet.from_dict(
            {"system": parse("builtins.currentSystem").expr},
        ),
    )
    return compact_nix_expr(import_nixpkgs.rebuild())


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
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_END,
        payload=CommandResult(args=args, returncode=0, stdout="", stderr=""),
    )
