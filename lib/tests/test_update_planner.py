"""Dependency planning contracts independent of Nix or asynchronous execution."""

import pytest

from lib.update.planner import (
    companion_source_depths,
    select_target_source_names,
    source_update_waves,
)


def test_deep_companion_chain_has_no_python_recursion_limit() -> None:
    """Selecting a leaf must include and order every transitive parent."""
    names = [f"source-{index}" for index in range(1500)]
    updaters = {
        name: type("Updater", (), {"companion_of": names[index - 1]} if index else {})
        for index, name in enumerate(names)
    }
    assert select_target_source_names((names[-1],), updaters) == names


def test_aggregate_depth_uses_longest_selected_dependency_path() -> None:
    """Shared ancestors and multiple edge kinds preserve deterministic ordering."""

    class Root:
        aggregate_into = ("aggregate",)

    class Child:
        companion_of = "root"
        aggregate_into = ("aggregate",)

    class Sibling:
        companion_of = "root"

    class Aggregate:
        companion_of = "child"

    updaters = {
        "aggregate": Aggregate,
        "child": Child,
        "sibling": Sibling,
        "root": Root,
    }
    assert companion_source_depths(set(updaters), updaters) == {
        "root": 0,
        "child": 1,
        "sibling": 1,
        "aggregate": 2,
    }
    assert select_target_source_names((), updaters) == [
        "root",
        "child",
        "sibling",
        "aggregate",
    ]
    assert source_update_waves(list(updaters), updaters) == [
        ["root"],
        ["child", "sibling"],
        ["aggregate"],
    ]


@pytest.mark.parametrize("self_cycle", [False, True])
def test_cycles_respect_selected_source_boundary(*, self_cycle: bool) -> None:
    """Reject self and mixed aggregate/companion cycles only when selected."""

    class Consumer:
        companion_of = "consumer" if self_cycle else "aggregate"
        aggregate_into = ("aggregate",)

    updaters = {"consumer": Consumer, "aggregate": object, "independent": object}
    assert select_target_source_names(("independent",), updaters) == ["independent"]
    with pytest.raises(RuntimeError, match="Companion source cycle detected"):
        select_target_source_names(("consumer",), updaters)
