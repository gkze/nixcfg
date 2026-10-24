"""Behavior tests for the pure flake.lock rollback policy."""

from lib.nix.models.flake_lock import FlakeLock
from lib.update.lock_policy import (
    LockRollback,
    RollbackUnavailable,
    prune_unreachable_lock_nodes,
    reachable_lock_nodes,
    rollback_input_locks,
)


def _node(repo: str, rev: str, **extra: object) -> dict[str, object]:
    return {
        "locked": {
            "type": "github",
            "owner": "o",
            "repo": repo,
            "rev": rev,
            "narHash": f"sha256-{repo}-{rev[:1]}",
        },
        "original": {"type": "github", "owner": "o", "repo": repo},
        **extra,
    }


def _lock(root_inputs: dict[str, object], **nodes: dict[str, object]) -> FlakeLock:
    return FlakeLock.model_validate({
        "root": "root",
        "version": 7,
        "nodes": {"root": {"inputs": root_inputs}, **nodes},
    })


def test_rollback_restores_failed_input_and_keeps_unrelated_updates() -> None:
    """Only the failed input's edge and node return to the baseline."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40),
        other=_node("other", "b" * 40),
    )
    current = _lock(
        {"tool": "tool_2", "other": "other"},
        tool_2=_node("tool", "c" * 40, inputs={"dep": "dep"}),
        dep=_node("dep", "d" * 40),
        other=_node("other", "e" * 40),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    assert result.inputs == ("tool",)
    assert result.lock.root_node.inputs == {"tool": "tool", "other": "other"}
    assert result.lock.nodes["tool"] == baseline.nodes["tool"]
    # The still-promoted input keeps its candidate node.
    assert result.lock.nodes["other"] == current.nodes["other"]
    # The superseded candidate node and its private dependency are pruned.
    assert set(result.lock.nodes) == {"root", "tool", "other"}
    # Inputs are not mutated in place.
    assert current.root_node.inputs == {"tool": "tool_2", "other": "other"}


def test_rollback_round_trips_the_lock_serialization() -> None:
    """A restored lock serializes exactly like the baseline it came from."""
    baseline = _lock({"tool": "tool"}, tool=_node("tool", "a" * 40, flake=False))
    current = _lock({"tool": "tool"}, tool=_node("tool", "c" * 40, flake=False))

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    dump = {"mode": "json", "by_alias": True, "exclude_unset": True}
    assert result.lock.model_dump(**dump) == baseline.model_dump(**dump)


def test_rollback_skips_follows_and_absent_baseline_inputs() -> None:
    """Inputs that follow another or did not exist cannot roll back alone."""
    baseline = _lock(
        {"tool": "tool", "alias": ["tool"], "dangling": "gone"},
        tool=_node("tool", "a" * 40),
    )
    current = _lock(
        {"tool": "tool", "alias": "alias", "dangling": "dangling", "new": "new"},
        tool=_node("tool", "a" * 40),
        alias=_node("alias", "b" * 40),
        dangling=_node("dangling", "c" * 40),
        new=_node("new", "d" * 40),
    )

    result = rollback_input_locks(
        baseline, current, frozenset({"tool", "alias", "dangling", "new"})
    )

    assert result == RollbackUnavailable(
        "failed inputs already match the baseline lock"
    )


def test_rollback_requires_both_root_nodes() -> None:
    """A lock without its root node has no input edges to restore."""
    good = _lock({"tool": "tool"}, tool=_node("tool", "a" * 40))
    rootless = FlakeLock.model_validate({"root": "missing", "version": 7, "nodes": {}})

    for baseline, current in ((rootless, good), (good, rootless)):
        assert rollback_input_locks(
            baseline, current, frozenset({"tool"})
        ) == RollbackUnavailable("lock root node is missing")


def test_reachability_terminates_on_cycles_and_ignores_follows_and_dangling() -> None:
    """Garbage collection keeps only nodes reachable through direct edges."""
    lock = _lock(
        {"self": "root", "leaf": "leaf", "missing": "missing", "alias": ["leaf"]},
        leaf=_node("leaf", "a" * 40, inputs={"back": "root"}),
        unreachable=_node("unreachable", "b" * 40),
    )

    assert reachable_lock_nodes(lock) == frozenset({"root", "leaf"})
    assert set(prune_unreachable_lock_nodes(lock).nodes) == {"root", "leaf"}
    assert "unreachable" in lock.nodes
