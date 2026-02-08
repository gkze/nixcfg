import functools
import shlex

from libnix.models.flake_lock import FlakeLock, FlakeLockNode
from libnix.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    UpdateEventKind,
)
from update.paths import FLAKE_LOCK_FILE


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

    return "\n".join(
        [
            "builtins.fetchTree { ",
            f'type = "{locked.type}"; ',
            f'owner = "{locked.owner}"; ',
            f'repo = "{locked.repo}"; ',
            f'rev = "{locked.rev}"; ',
            f'narHash = "{locked.narHash}"; ',
            " }",
        ]
    )


def nixpkgs_expr() -> str:
    node_name = get_root_input_name("nixpkgs")
    node = get_flake_input_node(node_name)
    return f"import ({flake_fetch_expr(node)}) {{ system = builtins.currentSystem; }}"


async def update_flake_input(input_name: str, *, source: str) -> EventStream:
    """Update a single flake input via :func:`libnix.commands.flake.nix_flake_lock_update`."""
    from libnix.commands.flake import nix_flake_lock_update

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
