"""Pure flake.lock rollback policy for partial promotion.

When an update run fails for some targets, the run keeps promoting the
targets that did not fail. A failed target's flake input can be rolled back
per input: point the root edge back at the baseline node, restore that node
and the dependency nodes it reaches, and drop nodes that no longer participate
in the graph. The rest of the lock keeps describing the candidate. A ref is
included only after its ``flake.nix`` declaration was restored the same way.

A dependency node that another kept pin still uses is copied under a new id,
so the kept pin keeps the candidate revision and the restored pin gets the
baseline revision. Follows edges are left alone: they name another input, not
a node this rollback owns.

This module owns that graph transformation on the typed :class:`FlakeLock`
model. Callers own the file I/O. A rollback that cannot be proven safe returns
an explicit :class:`RollbackUnavailable` reason instead of a silent no-op, so
the caller can fall back to the whole-file revert and report why.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib.nix.models.flake_lock import FlakeLock, FlakeLockNode


@dataclass(frozen=True, slots=True)
class RollbackUnavailable:
    """Per-input rollback cannot be applied; revert the flake files instead."""

    reason: str


@dataclass(frozen=True, slots=True)
class LockRollback:
    """A rolled-back lock and the inputs whose edges and nodes were restored."""

    lock: FlakeLock
    inputs: tuple[str, ...]


def _dependency_closure(
    nodes: Mapping[str, FlakeLockNode],
    start: str,
    root_id: str,
) -> frozenset[str] | None:
    """Return the string-edge closure of ``start``, or ``None`` if it dangles.

    The root node is not expanded. A dependency that points back at the root
    does not pull every other input into the closure. A missing node means the
    baseline graph cannot be restored as written.
    """
    if start == root_id:
        return None
    seen: set[str] = set()
    stack = [start]
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id == root_id:
            continue
        node = nodes.get(node_id)
        if node is None:
            return None
        seen.add(node_id)
        stack.extend(
            edge for edge in (node.inputs or {}).values() if isinstance(edge, str)
        )
    return frozenset(seen)


def _reachable_from(
    nodes: Mapping[str, FlakeLockNode],
    starts: set[str],
    root_id: str,
) -> frozenset[str]:
    """Return nodes reached from ``starts`` without walking the root."""
    reachable: set[str] = set()
    stack = list(starts)
    while stack:
        node_id = stack.pop()
        if node_id in reachable or node_id == root_id or node_id not in nodes:
            continue
        reachable.add(node_id)
        stack.extend(
            edge
            for edge in (nodes[node_id].inputs or {}).values()
            if isinstance(edge, str)
        )
    return frozenset(reachable)


def _fresh_node_id(node_id: str, used: set[str]) -> str:
    """Return a node id that is not already present in ``used``."""
    candidate = f"{node_id}_baseline"
    suffix = 2
    while candidate in used:
        candidate = f"{node_id}_baseline_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _closures_match(
    baseline_nodes: Mapping[str, FlakeLockNode],
    current_nodes: Mapping[str, FlakeLockNode],
    baseline_id: str,
    current_id: str,
    root_id: str,
    seen: set[tuple[str, str]] | None = None,
) -> bool:
    """Return whether the two nodes reach the same sources.

    Node ids may differ. A copied dependency (``crane_baseline``) still
    matches the baseline ``crane`` node, so applying a rollback twice does
    not rename it again.
    """
    if root_id in (baseline_id, current_id):
        return baseline_id == current_id
    seen = set() if seen is None else seen
    pair = (baseline_id, current_id)
    if pair in seen:
        return True
    seen.add(pair)
    return _node_closures_match(
        baseline_nodes, current_nodes, baseline_id, current_id, root_id, seen
    )


def _node_closures_match(
    baseline_nodes: Mapping[str, FlakeLockNode],
    current_nodes: Mapping[str, FlakeLockNode],
    baseline_id: str,
    current_id: str,
    root_id: str,
    seen: set[tuple[str, str]],
) -> bool:
    """Compare one pair of nodes and their input edges."""
    baseline = baseline_nodes.get(baseline_id)
    current = current_nodes.get(current_id)
    if baseline is None or current is None:
        return False
    if baseline.model_dump(
        mode="json", exclude={"inputs"}, exclude_unset=True
    ) != current.model_dump(mode="json", exclude={"inputs"}, exclude_unset=True):
        return False
    baseline_inputs = baseline.inputs or {}
    current_inputs = current.inputs or {}
    if set(baseline_inputs) != set(current_inputs):
        return False
    return all(
        _input_edges_match(
            baseline_nodes,
            current_nodes,
            baseline_inputs[name],
            current_inputs[name],
            root_id,
            seen,
        )
        for name in baseline_inputs
    )


def _input_edges_match(
    baseline_nodes: Mapping[str, FlakeLockNode],
    current_nodes: Mapping[str, FlakeLockNode],
    baseline_edge: str | list[str],
    current_edge: str | list[str],
    root_id: str,
    seen: set[tuple[str, str]],
) -> bool:
    """Return whether one input edge reaches the same source on both sides."""
    if isinstance(baseline_edge, str) and isinstance(current_edge, str):
        return _closures_match(
            baseline_nodes, current_nodes, baseline_edge, current_edge, root_id, seen
        )
    return baseline_edge == current_edge


def _with_remapped_inputs(
    node: FlakeLockNode,
    mapping: Mapping[str, str],
) -> FlakeLockNode:
    """Return ``node`` with string edges rewritten through ``mapping``."""
    if not node.inputs:
        return node.model_copy(deep=True)
    return node.model_copy(
        deep=True,
        update={
            "inputs": {
                name: mapping[edge]
                if isinstance(edge, str) and edge in mapping
                else edge
                for name, edge in node.inputs.items()
            }
        },
    )


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
    """Restore ``input_names``' root edges and dependency closures.

    Inputs that already match the baseline, including every node they reach,
    are skipped. An input that follows another input in the baseline (a
    list-valued edge), whose baseline node is absent, or whose baseline
    closure dangles cannot be rolled back on its own; it is skipped and the
    caller's whole-file fallback covers it. A dependency still used by a pin
    that is staying is copied under a fresh node id instead of being
    overwritten. Returns :class:`RollbackUnavailable` when no input changed,
    so callers can distinguish "nothing to do" from a successful partial
    rollback.
    """
    if baseline.root not in baseline.nodes or current.root not in current.nodes:
        return RollbackUnavailable("lock root node is missing")
    baseline_edges = baseline.root_node.inputs or {}
    current_edges = dict(current.root_node.inputs or {})
    nodes = dict(current.nodes)
    kept = {
        edge
        for name, edge in current_edges.items()
        if name not in input_names and isinstance(edge, str)
    }
    kept_nodes = _reachable_from(nodes, kept, current.root)
    planned: list[tuple[str, str, frozenset[str]]] = []
    satisfied: list[str] = []
    refused = False
    for input_name in sorted(input_names):
        base_edge = baseline_edges.get(input_name)
        if not isinstance(base_edge, str) or base_edge not in baseline.nodes:
            continue
        closure = _dependency_closure(baseline.nodes, base_edge, baseline.root)
        if closure is None:
            refused = True
            continue
        current_edge = current_edges.get(input_name)
        if isinstance(current_edge, str) and _closures_match(
            baseline.nodes, nodes, base_edge, current_edge, baseline.root
        ):
            satisfied.append(input_name)
            continue
        planned.append((input_name, base_edge, closure))
    if not planned:
        reason = (
            "baseline lock does not contain the failed input's dependencies"
            if refused
            else "failed inputs already match the baseline lock"
        )
        return RollbackUnavailable(reason)
    used = set(nodes)
    mapping: dict[str, str] = {}
    for _input_name, _base_edge, closure in planned:
        for node_id in sorted(closure):
            if node_id in mapping:
                continue
            current_node = nodes.get(node_id)
            base_node = baseline.nodes[node_id]
            if (
                current_node is not None
                and current_node != base_node
                and node_id in kept_nodes
            ):
                mapping[node_id] = _fresh_node_id(node_id, used)
            else:
                mapping[node_id] = node_id
    rolled = list(satisfied)
    for input_name, base_edge, closure in planned:
        current_edges[input_name] = mapping[base_edge]
        for node_id in closure:
            nodes[mapping[node_id]] = _with_remapped_inputs(
                baseline.nodes[node_id], mapping
            )
        rolled.append(input_name)
    nodes[current.root] = current.root_node.model_copy(update={"inputs": current_edges})
    lock = prune_unreachable_lock_nodes(current.model_copy(update={"nodes": nodes}))
    return LockRollback(lock=lock, inputs=tuple(sorted(rolled)))


def render_lock(lock: FlakeLock) -> str:
    """Return ``lock`` as Nix writes it: sorted, two-space-indented JSON.

    A prepare that rolls back an input and a fold that applies the same hold
    to another platform's lock must produce identical bytes, so both render
    here.
    """
    payload = lock.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "LockRollback",
    "RollbackUnavailable",
    "prune_unreachable_lock_nodes",
    "reachable_lock_nodes",
    "render_lock",
    "rollback_input_locks",
]
