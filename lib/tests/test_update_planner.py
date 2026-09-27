"""Dependency planning contracts independent of Nix or asynchronous execution."""

import pytest

from lib.update.planner import (
    companion_source_depths,
    held_bulk_update_input_names,
    resolve_update_targets,
    select_target_source_names,
)
from lib.update.refs import FlakeInputRef


@pytest.mark.parametrize("relationship", ["companion", "aggregate"])
def test_bulk_hold_preserves_coupled_sources_and_allows_explicit_retry(
    relationship: str,
) -> None:
    """Bulk runs retain held artifacts; deliberate retries still resolve dependencies."""
    held = type("Held", (), {"bulk_update_hold": "Upstream source mismatch"})
    coupled = type(
        "Coupled",
        (),
        {"companion_of": "held"}
        if relationship == "companion"
        else {"aggregate_into": ("held",)},
    )
    updaters = {"held": held, "coupled": coupled, "other": object}
    assert select_target_source_names((), updaters) == ["other"]
    assert "held" in select_target_source_names(("held",), updaters)
    assert set(select_target_source_names(("coupled",), updaters)) == {
        "held",
        "coupled",
    }
    assert select_target_source_names(("other",), updaters) == ["other"]


def test_bulk_hold_skips_backing_flake_inputs_on_untargeted_runs() -> None:
    """Held sources must keep their flake input pin during bulk ref refresh."""

    class Held:
        bulk_update_hold = "Hosted macos-15 cannot rebuild this from source"
        input_name = "goose"

    class ExtraHeld:
        bulk_update_hold = "Companion pin"
        additional_input_names = ("goose-v8",)

    class Other:
        pass

    class Opts:
        target_names: tuple[str, ...] = ()
        no_refs = False
        native_only = False
        no_sources = False
        no_input = False
        check = False

    updaters = {"goose-cli": Held, "goose-extra": ExtraHeld, "other": Other}
    refs = [
        FlakeInputRef("goose", "aaif-goose", "goose", "v1.51.0", "github"),
        FlakeInputRef("goose-v8", "aaif-goose", "goose-v8", "v1.0.0", "github"),
        FlakeInputRef("nixpkgs", "NixOS", "nixpkgs", "nixos-unstable", "github"),
    ]
    assert held_bulk_update_input_names(updaters) == {"goose", "goose-v8"}

    resolved = resolve_update_targets(
        Opts(),
        updaters=updaters,
        ref_inputs=refs,
        result_type=lambda **kwargs: kwargs,
    )
    assert [item.name for item in resolved["ref_inputs"]] == ["nixpkgs"]
    assert resolved["source_names"] == ["other"]

    class Explicit(Opts):
        target_names = ("goose",)

    explicit = resolve_update_targets(
        Explicit(),
        updaters=updaters,
        ref_inputs=refs,
        result_type=lambda **kwargs: kwargs,
    )
    assert [item.name for item in explicit["ref_inputs"]] == ["goose"]


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


def test_dependency_clusters_couple_companions_aggregates_and_inputs() -> None:
    """Targets whose files derive from each other, or from one input, cluster."""
    from lib.nix.models.sources import SourceEntry
    from lib.update.planner import dependency_clusters, failure_closure
    from lib.update.updaters import Updater

    class _Parent(Updater):
        pass

    class _Child(Updater):
        companion_of = "parent"

    class _Member(Updater):
        aggregate_into = ("agg",)

    class _Agg(Updater):
        pass

    class _Backed(Updater):
        input_name = "tool"

    class _Extra(Updater):
        additional_input_names = ("other",)

    class _Solo(Updater):
        pass

    class _Orphan(Updater):
        companion_of = "missing"

    updaters: dict[str, type[object]] = {
        "parent": _Parent,
        "child": _Child,
        "member": _Member,
        "agg": _Agg,
        "backed": _Backed,
        "extra": _Extra,
        "solo": _Solo,
        "orphan": _Orphan,
    }
    names = ["parent", "child", "member", "agg", "backed", "extra", "solo", "orphan"]
    clusters = dependency_clusters(
        names,
        updaters=updaters,
        entries={"solo": SourceEntry(hashes={}, input="tool")},
        input_names=["tool", "other", "unused"],
    )

    assert clusters["parent"] == clusters["child"] == {"parent", "child"}
    assert clusters["member"] == clusters["agg"] == {"member", "agg"}
    assert clusters["backed"] == clusters["tool"] == {"backed", "solo", "tool"}
    assert clusters["extra"] == {"extra", "other"}
    assert clusters["orphan"] == {"orphan"}
    assert clusters["unused"] == {"unused"}
    assert dependency_clusters(["x"], updaters={})["x"] == {"x"}

    assert failure_closure(["child"], clusters) == {"parent", "child"}
    assert failure_closure(["solo", "member"], clusters) == {
        "backed",
        "solo",
        "tool",
        "member",
        "agg",
    }
    assert failure_closure(["unknown"], clusters) == {"unknown"}
    assert failure_closure([], clusters) == frozenset()
