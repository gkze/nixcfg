"""Target planning helpers for update runs."""

from typing import TYPE_CHECKING, Protocol

from lib.update.updaters.flake_backed import FlakeInputUpdater

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from lib.nix.models.sources import SourceEntry
    from lib.update.refs import FlakeInputRef


class _UpdateOptionsLike(Protocol):
    @property
    def target_names(self) -> tuple[str, ...]: ...

    @property
    def no_refs(self) -> bool: ...

    @property
    def native_only(self) -> bool: ...

    @property
    def no_sources(self) -> bool: ...

    @property
    def no_input(self) -> bool: ...

    @property
    def check(self) -> bool: ...


def source_backing_input_name(
    name: str,
    updater_cls: type[object] | None,
    entry: SourceEntry | None = None,
) -> str | None:
    """Return the flake input that backs one source, if any."""
    if updater_cls is not None:
        input_name = getattr(updater_cls, "input_name", None)
        if isinstance(input_name, str) and input_name:
            return input_name
        if issubclass(updater_cls, FlakeInputUpdater):
            return name
    if entry is not None and entry.input:
        return entry.input
    return None


def source_additional_input_names(
    updater_cls: type[object] | None,
) -> tuple[str, ...]:
    """Return auxiliary flake inputs consumed by one source updater."""
    if updater_cls is None:
        return ()
    input_names = getattr(updater_cls, "additional_input_names", ())
    if not isinstance(input_names, tuple) or not all(
        isinstance(name, str) and name for name in input_names
    ):
        msg = "additional_input_names must be a tuple of non-empty input names"
        raise TypeError(msg)
    if len(input_names) != len(set(input_names)):
        msg = "additional_input_names must be unique"
        raise RuntimeError(msg)
    return input_names


def companion_source_name(updater_cls: type[object] | None) -> str | None:
    """Return the parent source for one companion updater class."""
    if updater_cls is None:
        return None
    companion_of = getattr(updater_cls, "companion_of", None)
    return companion_of if isinstance(companion_of, str) and companion_of else None


def companion_source_parent(
    updaters: Mapping[str, type[object]],
    name: str,
) -> str | None:
    """Return the direct companion parent for one source name."""
    return companion_source_name(updaters.get(name))


def aggregate_destination_names(updater_cls: type[object] | None) -> tuple[str, ...]:
    """Return aggregate sources fed by one updater class."""
    if updater_cls is None:
        return ()
    aggregate_into = getattr(updater_cls, "aggregate_into", ())
    if not isinstance(aggregate_into, tuple) or not all(
        isinstance(name, str) and name for name in aggregate_into
    ):
        msg = "aggregate_into must be a tuple of non-empty source names"
        raise TypeError(msg)
    if len(aggregate_into) != len(set(aggregate_into)):
        msg = "aggregate_into source names must be unique"
        raise RuntimeError(msg)
    return aggregate_into


def aggregate_source_members(
    updaters: Mapping[str, type[object]],
    aggregate_name: str,
) -> tuple[str, ...]:
    """Return registered sources that contribute to one aggregate."""
    return tuple(
        name
        for name, updater_cls in updaters.items()
        if aggregate_name in aggregate_destination_names(updater_cls)
    )


def source_prerequisites(
    updaters: Mapping[str, type[object]],
    name: str,
    *,
    selected: set[str] | None = None,
) -> tuple[str, ...]:
    """Return direct, selected prerequisites for one source update."""
    prerequisites = [
        prerequisite
        for prerequisite in (
            companion_source_parent(updaters, name),
            *aggregate_source_members(updaters, name),
        )
        if prerequisite is not None and (selected is None or prerequisite in selected)
    ]
    return tuple(dict.fromkeys(prerequisites))


def companion_source_depths(
    names: set[str],
    updaters: Mapping[str, type[object]],
) -> dict[str, int]:
    """Return dependency depth for each selected source."""
    memo: dict[str, int] = {}
    visiting: list[str] = []

    def _depth(name: str) -> int:
        if name in memo:
            return memo[name]
        if name in visiting:
            cycle = " -> ".join((*visiting, name))
            msg = f"Companion source cycle detected: {cycle}"
            raise RuntimeError(msg)

        visiting.append(name)
        prerequisites = source_prerequisites(updaters, name, selected=names)
        value = (
            0
            if not prerequisites
            else max(_depth(prerequisite) for prerequisite in prerequisites) + 1
        )
        visiting.pop()
        memo[name] = value
        return value

    for name in sorted(names):
        _depth(name)
    return memo


def add_companion_source_parents(
    names: set[str],
    updaters: Mapping[str, type[object]],
) -> None:
    """Expand *names* with transitive companion parents."""
    visited: set[str] = set()
    visiting: list[str] = []

    def _visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            cycle = " -> ".join((*visiting, name))
            msg = f"Companion source cycle detected: {cycle}"
            raise RuntimeError(msg)

        visiting.append(name)
        parent = companion_source_parent(updaters, name)
        if parent is not None and parent in updaters:
            names.add(parent)
            _visit(parent)
        visiting.pop()
        visited.add(name)

    for name in sorted(names):
        _visit(name)


def add_companion_source_children(
    names: set[str],
    *,
    roots: set[str],
    updaters: Mapping[str, type[object]],
) -> None:
    """Expand *names* with companion children rooted at explicit targets."""
    frontier = sorted(roots)
    visited: set[str] = set()
    while frontier:
        parent = frontier.pop(0)
        if parent in visited:
            continue
        visited.add(parent)
        for name, updater_cls in updaters.items():
            companion_of = companion_source_name(updater_cls)
            if companion_of == parent and name not in names:
                names.add(name)
                frontier.append(name)


def add_aggregate_sources(
    names: set[str],
    updaters: Mapping[str, type[object]],
) -> None:
    """Select every aggregate fed by a selected source."""
    while True:
        destinations = {
            destination
            for name in names
            for destination in aggregate_destination_names(updaters.get(name))
        }
        missing = sorted(destinations.difference(updaters))
        if missing:
            joined = ", ".join(missing)
            msg = f"Aggregate source is not registered: {joined}"
            raise RuntimeError(msg)
        additions = destinations.difference(names)
        if not additions:
            return
        names.update(additions)


def select_target_source_names(
    target_names: tuple[str, ...],
    updaters: Mapping[str, type[object]],
) -> list[str]:
    """Resolve targets, expanding input consumers and source dependencies."""
    if not target_names:
        selected = set(updaters)
        roots = set(selected)
        order = {name: index for index, name in enumerate(updaters)}
    else:
        selected: set[str] = set()
        roots: set[str] = set()
        order: dict[str, int] = {}
        for target in target_names:
            target_sources = [
                name
                for name, updater_cls in updaters.items()
                if source_backing_input_name(name, updater_cls, None) == target
                or target in source_additional_input_names(updater_cls)
            ]
            if not target_sources and target in updaters:
                target_sources = [target]
            for name in target_sources:
                selected.add(name)
                roots.add(name)
                order.setdefault(name, len(order))
        if not selected:
            return []

    add_companion_source_parents(selected, updaters)
    add_companion_source_children(selected, roots=roots, updaters=updaters)
    add_aggregate_sources(selected, updaters)

    depths = companion_source_depths(selected, updaters)
    return sorted(
        selected,
        key=lambda name: (
            depths[name],
            order.get(name, len(order)),
            name,
        ),
    )


def source_update_waves(
    source_names: Sequence[str],
    updaters: Mapping[str, type[object]],
) -> list[list[str]]:
    """Group source updates into dependency-respecting execution waves."""
    if not source_names:
        return []

    depths = companion_source_depths(set(source_names), updaters)
    max_depth = max(depths.values(), default=0)
    return [
        [name for name in source_names if depths[name] == depth]
        for depth in range(max_depth + 1)
    ]


def resolve_update_targets[ResolvedTargetsT](
    opts: _UpdateOptionsLike,
    *,
    updaters: Mapping[str, type[object]],
    ref_inputs: list[FlakeInputRef],
    result_type: Callable[..., ResolvedTargetsT],
) -> ResolvedTargetsT:
    """Resolve target sets and operational flags from update options."""
    all_source_names = set(updaters.keys())
    all_ref_names = {i.name for i in ref_inputs}
    all_additional_input_names = {
        input_name
        for updater_cls in updaters.values()
        for input_name in source_additional_input_names(updater_cls)
    }
    all_known_names = all_source_names | all_ref_names | all_additional_input_names

    target_names = opts.target_names
    source_names = select_target_source_names(target_names, updaters)

    # --native-only computes current-platform source hashes, not input refs.
    do_refs = not opts.no_refs and not opts.native_only
    do_sources = not opts.no_sources
    if target_names:
        if not any(target in all_ref_names for target in target_names):
            do_refs = False
        if not source_names:
            do_sources = False

    selected_ref_inputs = (
        [i for i in ref_inputs if i.name in set(target_names)]
        if target_names
        else ref_inputs
    )
    if not do_refs:
        selected_ref_inputs = []
    if not do_sources:
        source_names = []

    return result_type(
        all_source_names=all_source_names,
        all_ref_inputs=ref_inputs,
        all_ref_names=all_ref_names,
        all_known_names=all_known_names,
        do_refs=do_refs,
        do_sources=do_sources,
        do_input_refresh=not opts.no_input,
        dry_run=opts.check,
        native_only=opts.native_only,
        ref_inputs=selected_ref_inputs,
        source_names=source_names,
    )


__all__ = [
    "add_aggregate_sources",
    "add_companion_source_children",
    "add_companion_source_parents",
    "aggregate_destination_names",
    "aggregate_source_members",
    "companion_source_depths",
    "companion_source_name",
    "companion_source_parent",
    "resolve_update_targets",
    "select_target_source_names",
    "source_additional_input_names",
    "source_backing_input_name",
    "source_prerequisites",
    "source_update_waves",
]
