"""Pure flake.lock rollback policy for partial promotion.

When an update run fails for some targets, the run keeps promoting the
targets that did not fail. A failed target whose flake input is lock-only
(no ref pinned in ``flake.nix``) can be rolled back per input: point the root
edge back at the baseline node, restore that node, and drop nodes that no
longer participate in the graph. The rest of the lock keeps describing the
candidate.

This module owns that graph transformation on the typed :class:`FlakeLock`
model. Callers own the file I/O. A rollback that cannot be proven safe returns
an explicit :class:`RollbackUnavailable` reason instead of a silent no-op, so
the caller can fall back to the whole-file revert and report why.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.nix.models.flake_lock import FlakeLock


@dataclass(frozen=True, slots=True)
class RollbackUnavailable:
    """Per-input rollback cannot be applied; revert the flake files instead."""

    reason: str


@dataclass(frozen=True, slots=True)
class LockRollback:
    """A rolled-back lock and the inputs whose edges and nodes were restored."""

    lock: FlakeLock
    inputs: tuple[str, ...]


def reachable_lock_nodes(lock: FlakeLock) -> frozenset[str]:
    """Return the node IDs reachable from the root through direct edges.

    Follows paths (list-valued edges) name an existing route through other
    edges, so they never keep a node alive on their own.
    """
    reachable: set[str] = set()
    stack = [lock.root]
    while stack:
        node_id = stack.pop()
        if node_id in reachable or node_id not in lock.nodes:
            continue
        reachable.add(node_id)
        stack.extend(
            edge
            for edge in (lock.nodes[node_id].inputs or {}).values()
            if isinstance(edge, str)
        )
    return frozenset(reachable)


def prune_unreachable_lock_nodes(lock: FlakeLock) -> FlakeLock:
    """Return ``lock`` without nodes that no longer participate in the graph."""
    reachable = reachable_lock_nodes(lock)
    return lock.model_copy(
        update={
            "nodes": {
                node_id: node
                for node_id, node in lock.nodes.items()
                if node_id in reachable
            }
        }
    )


def rollback_input_locks(
    baseline: FlakeLock,
    current: FlakeLock,
    input_names: frozenset[str],
) -> LockRollback | RollbackUnavailable:
    """Restore ``input_names``' root edges and nodes from ``baseline``.

    Inputs that already match the baseline are skipped. An input that follows
    another input in the baseline (a list-valued edge) or whose baseline node
    is absent cannot be rolled back on its own; it is skipped and the caller's
    whole-file fallback covers it. Returns :class:`RollbackUnavailable` when
    no input changed, so callers can distinguish "nothing to do" from a
    successful partial rollback.
    """
    if baseline.root not in baseline.nodes or current.root not in current.nodes:
        return RollbackUnavailable("lock root node is missing")
    baseline_edges = baseline.root_node.inputs or {}
    current_edges = dict(current.root_node.inputs or {})
    nodes = dict(current.nodes)
    rolled: list[str] = []
    for input_name in sorted(input_names):
        base_edge = baseline_edges.get(input_name)
        if not isinstance(base_edge, str) or base_edge not in baseline.nodes:
            continue
        base_node = baseline.nodes[base_edge]
        if (
            current_edges.get(input_name) == base_edge
            and nodes.get(base_edge) == base_node
        ):
            continue
        current_edges[input_name] = base_edge
        nodes[base_edge] = base_node.model_copy(deep=True)
        rolled.append(input_name)
    if not rolled:
        return RollbackUnavailable("failed inputs already match the baseline lock")
    nodes[current.root] = current.root_node.model_copy(update={"inputs": current_edges})
    lock = prune_unreachable_lock_nodes(current.model_copy(update={"nodes": nodes}))
    return LockRollback(lock=lock, inputs=tuple(rolled))


__all__ = [
    "LockRollback",
    "RollbackUnavailable",
    "prune_unreachable_lock_nodes",
    "reachable_lock_nodes",
    "rollback_input_locks",
]
