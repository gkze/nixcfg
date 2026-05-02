"""Target planning helpers for update runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from lib.update.refs import FlakeInputRef


class _UpdateOptionsLike(Protocol):
    @property
    def target_names(self) -> tuple[str, ...]: ...

    no_refs: bool
    native_only: bool
    no_sources: bool
    no_input: bool
    check: bool


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
        parent = companion_source_parent(updaters, name)
        value = 0 if parent is None or parent not in names else _depth(parent) + 1
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


def select_target_source_names(
    target_names: tuple[str, ...],
    updaters: Mapping[str, type[object]],
    *,
    source_backing_input_name: Callable[..., str | None],
) -> list[str]:
    """Resolve source targets, expanding backing-input and companion sources."""
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

    depths = companion_source_depths(selected, updaters)
    return sorted(
        selected,
        key=lambda name: (
            depths[name],
            order.get(name, len(order)),
            name,
        ),
    )


def select_source_names(
    source: str | None,
    updaters: Mapping[str, type[object]],
    *,
    source_backing_input_name: Callable[..., str | None],
) -> list[str]:
    """Resolve one legacy source target into updater source names."""
    target_names = () if source is None else (source,)
    return select_target_source_names(
        target_names,
        updaters,
        source_backing_input_name=source_backing_input_name,
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
    source_backing_input_name: Callable[..., str | None],
    result_type: type[ResolvedTargetsT],
) -> ResolvedTargetsT:
    """Resolve target sets and operational flags from update options."""
    all_source_names = set(updaters.keys())
    all_ref_names = {i.name for i in ref_inputs}
    all_known_names = all_source_names | all_ref_names

    target_names = opts.target_names
    source_names = select_target_source_names(
        target_names,
        updaters,
        source_backing_input_name=source_backing_input_name,
    )

    # --native-only implies --no-refs: in CI, refs are managed by the pipeline.
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
    "add_companion_source_children",
    "add_companion_source_parents",
    "companion_source_depths",
    "companion_source_name",
    "companion_source_parent",
    "resolve_update_targets",
    "select_source_names",
    "select_target_source_names",
    "source_update_waves",
]
