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


def test_rollback_restores_private_dependency_nodes() -> None:
    """A restored flake input gets the dependency nodes its baseline reached."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"crane": "crane", "nixpkgs": ["nixpkgs"]}),
        crane=_node("crane", "c" * 40),
        other=_node("other", "b" * 40),
    )
    current = _lock(
        {"tool": "tool_2", "other": "other"},
        tool_2=_node("tool", "d" * 40, inputs={"crane": "crane_2"}),
        crane_2=_node("crane", "e" * 40),
        other=_node("other", "f" * 40),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    assert result.inputs == ("tool",)
    assert result.lock.root_node.inputs == {"tool": "tool", "other": "other"}
    assert result.lock.nodes["tool"].locked == baseline.nodes["tool"].locked
    assert result.lock.nodes["tool"].inputs == {
        "crane": "crane",
        "nixpkgs": ["nixpkgs"],
    }
    assert result.lock.nodes["crane"].locked == baseline.nodes["crane"].locked
    assert result.lock.nodes["other"].locked == current.nodes["other"].locked
    assert set(result.lock.nodes) == {"root", "tool", "crane", "other"}


def test_rollback_copies_a_dependency_another_pin_still_uses() -> None:
    """A shared dependency keeps the candidate revision under its original id."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "c" * 40),
        other=_node("other", "b" * 40, inputs={"crane": "crane"}),
    )
    current = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "d" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "e" * 40),
        other=_node("other", "f" * 40, inputs={"crane": "crane"}),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    assert result.lock.nodes["tool"].locked.rev == "a" * 40
    assert result.lock.nodes["tool"].inputs == {"crane": "crane_baseline"}
    assert result.lock.nodes["crane_baseline"].locked.rev == "c" * 40
    assert result.lock.nodes["other"].locked.rev == "f" * 40
    assert result.lock.nodes["crane"].locked.rev == "e" * 40


def test_rollback_disambiguates_a_fresh_dependency_id() -> None:
    """A copied dependency does not reuse an id the candidate already has."""
    baseline = _lock(
        {"tool": "tool"},
        tool=_node("tool", "a" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "c" * 40),
    )
    current = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "d" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "e" * 40),
        crane_baseline=_node("crane", "1" * 40),
        other=_node("other", "f" * 40, inputs={"crane": "crane"}),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    assert result.lock.nodes["tool"].inputs == {"crane": "crane_baseline_2"}
    assert result.lock.nodes["crane_baseline_2"].locked.rev == "c" * 40
    assert "crane_baseline" not in result.lock.nodes


def test_rollback_matches_a_cycle_that_points_at_the_root() -> None:
    """An unchanged cycle, including an edge back to the root, is left alone."""
    tool = _node("tool", "a" * 40, inputs={"back": "root", "dep": "dep"})
    dep = _node("dep", "c" * 40, inputs={"tool": "tool"})
    other = _node("other", "b" * 40, inputs={"back": "root"})
    baseline = _lock(
        {"tool": "tool", "other": "other"}, tool=tool, dep=dep, other=other
    )
    current = _lock({"tool": "tool", "other": "other"}, tool=tool, dep=dep, other=other)

    assert rollback_input_locks(
        baseline, current, frozenset({"tool"})
    ) == RollbackUnavailable("failed inputs already match the baseline lock")


def test_rollback_restores_when_the_dependency_shape_disagrees() -> None:
    """A missing node or a different input map is not treated as already restored."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"dep": "dep", "extra": "extra"}),
        dep=_node("dep", "c" * 40),
        extra=_node("extra", "e" * 40),
        other=_node("other", "b" * 40),
    )
    missing = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"dep": "dep", "extra": "extra"}),
        other=_node("other", "b" * 40),
    )
    fewer_inputs = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"dep": "dep"}),
        dep=_node("dep", "c" * 40),
        other=_node("other", "b" * 40),
    )
    points_at_root = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"dep": "root", "extra": "extra"}),
        extra=_node("extra", "e" * 40),
        other=_node("other", "b" * 40),
    )

    for current in (missing, fewer_inputs, points_at_root):
        result = rollback_input_locks(baseline, current, frozenset({"tool"}))
        assert isinstance(result, LockRollback)
        assert result.lock.nodes["dep"].locked.rev == "c" * 40
        assert result.lock.nodes["extra"].locked.rev == "e" * 40
        assert result.lock.nodes["other"].locked.rev == "b" * 40


def test_rollback_of_a_copied_dependency_is_idempotent() -> None:
    """A second rollback recognizes the dependency copied on the first."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "c" * 40),
        other=_node("other", "b" * 40, inputs={"crane": "crane"}),
    )
    current = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "d" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "e" * 40),
        other=_node("other", "f" * 40, inputs={"crane": "crane"}),
    )

    first = rollback_input_locks(baseline, current, frozenset({"tool"}))
    assert isinstance(first, LockRollback)
    assert rollback_input_locks(
        baseline, first.lock, frozenset({"tool"})
    ) == RollbackUnavailable("failed inputs already match the baseline lock")


def test_rollback_refuses_a_dangling_baseline_dependency() -> None:
    """A baseline edge to a missing node is not restored halfway."""
    baseline = _lock(
        {"tool": "tool"},
        tool=_node("tool", "a" * 40, inputs={"crane": "missing"}),
    )
    current = _lock(
        {"tool": "tool"},
        tool=_node("tool", "d" * 40),
    )

    assert rollback_input_locks(
        baseline, current, frozenset({"tool"})
    ) == RollbackUnavailable(
        "baseline lock does not contain the failed input's dependencies"
    )


def test_rollback_stops_a_dependency_cycle_at_the_root() -> None:
    """A node that points back at the root does not restore every other pin."""
    baseline = _lock(
        {"tool": "tool", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"back": "root", "dep": "dep"}),
        dep=_node("dep", "c" * 40, inputs={"tool": "tool"}),
        other=_node("other", "b" * 40),
    )
    current = _lock(
        {"tool": "tool_2", "other": "other"},
        tool_2=_node("tool", "d" * 40),
        other=_node("other", "e" * 40),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool"}))

    assert isinstance(result, LockRollback)
    assert set(result.lock.nodes) == {"root", "tool", "dep", "other"}
    assert result.lock.nodes["other"].locked.rev == "e" * 40
    assert result.lock.nodes["dep"].inputs == {"tool": "tool"}


def test_rollback_leaves_an_unchanged_dependency_closure() -> None:
    """A pin whose dependency nodes already match is not reported as rolled."""
    node = _node("tool", "a" * 40, inputs={"crane": "crane"})
    crane = _node("crane", "c" * 40)
    baseline = _lock({"tool": "tool"}, tool=node, crane=crane)
    current = _lock(
        {"tool": "tool", "other": "other"},
        tool=node,
        crane=crane,
        other=_node("other", "b" * 40),
    )

    assert rollback_input_locks(
        baseline, current, frozenset({"tool"})
    ) == RollbackUnavailable("failed inputs already match the baseline lock")


def test_rollback_shares_one_copied_dependency() -> None:
    """Two restored inputs copy one colliding dependency, not two."""
    baseline = _lock(
        {"tool": "tool", "extra": "extra", "other": "other"},
        tool=_node("tool", "a" * 40, inputs={"crane": "crane"}),
        extra=_node("extra", "b" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "c" * 40),
        other=_node("other", "d" * 40, inputs={"crane": "crane"}),
    )
    current = _lock(
        {"tool": "tool", "extra": "extra", "other": "other"},
        tool=_node("tool", "e" * 40, inputs={"crane": "crane"}),
        extra=_node("extra", "f" * 40, inputs={"crane": "crane"}),
        crane=_node("crane", "1" * 40),
        other=_node("other", "2" * 40, inputs={"crane": "crane"}),
    )

    result = rollback_input_locks(baseline, current, frozenset({"tool", "extra"}))

    assert isinstance(result, LockRollback)
    assert result.inputs == ("extra", "tool")
    assert result.lock.nodes["tool"].inputs == {"crane": "crane_baseline"}
    assert result.lock.nodes["extra"].inputs == {"crane": "crane_baseline"}
    assert result.lock.nodes["other"].inputs == {"crane": "crane"}
    assert result.lock.nodes["crane"].locked.rev == "1" * 40
    assert result.lock.nodes["crane_baseline"].locked.rev == "c" * 40


def test_rollback_refuses_an_input_that_points_at_the_root() -> None:
    """The root node is not a source that can be restored as a dependency."""
    baseline = _lock({"tool": "root"}, tool=_node("tool", "a" * 40))
    current = _lock({"tool": "tool"}, tool=_node("tool", "d" * 40))

    assert rollback_input_locks(
        baseline, current, frozenset({"tool"})
    ) == RollbackUnavailable(
        "baseline lock does not contain the failed input's dependencies"
    )


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
