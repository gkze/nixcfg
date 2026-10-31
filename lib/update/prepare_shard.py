"""Disjoint prepare slices for the public macOS runner pool.

Each shard keeps the requested target set. Connected components of the
prerequisite graph stay on one runner: a companion parent or aggregate
member is hashed before its consumer, against the updated input. Independent
components are packed largest-first. Names inside a shard stay in plan
order. The same-platform merge accepts the shards only when those slices
are that assignment and no prerequisite edge crosses shards.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

# Public repositories get five hosted macos-15 runners. Darwin prepare uses
# the whole pool and no larger SKU.
DARWIN_PREPARE_SHARD_COUNT = 5
_COVERAGE_KEYS = frozenset({"plan", "hashed", "shard", "count"})


def partition_prepare_targets(
    names: tuple[str, ...] | list[str],
    *,
    shard: int,
    count: int,
    prerequisites: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """Return the plan names assigned to *shard*.

    Edges are undirected for membership and directed when the fold checks
    that a consumer and its prerequisite share a runner. Components are
    assigned largest first. Equal sizes break ties by the component's
    earliest name. Equal shard loads break ties by the lower shard index.
    The returned names stay in plan order.
    """
    _require_shard_slot(shard, count)
    plan = tuple(names)
    if len(set(plan)) != len(plan):
        msg = "Prepare plan names must be unique"
        raise ValueError(msg)
    assigned = _assign_prepare_components(
        plan,
        {} if prerequisites is None else prerequisites,
        count,
    )
    return tuple(name for name in plan if assigned[name] == shard)


def _assign_prepare_components(
    plan: tuple[str, ...],
    prerequisites: Mapping[str, Sequence[str]],
    count: int,
) -> dict[str, int]:
    """Pack each prerequisite component onto one shard."""
    loads = [0] * count
    assigned: dict[str, int] = {}
    for component in _prerequisite_components(plan, prerequisites):
        chosen = min(range(count), key=lambda index: (loads[index], index))
        loads[chosen] += len(component)
        for name in component:
            assigned[name] = chosen
    return assigned


def _prerequisite_components(
    plan: tuple[str, ...],
    prerequisites: Mapping[str, Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    """Return undirected components, largest first, ties by earliest name."""
    parent = {name: name for name in plan}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left: str, right: str) -> None:
        if right not in parent:
            return
        parent[find(left)] = find(right)

    for name in plan:
        for prerequisite in prerequisites.get(name, ()):
            union(name, prerequisite)

    order = {name: index for index, name in enumerate(plan)}
    grouped: dict[str, list[str]] = {}
    for name in plan:
        grouped.setdefault(find(name), []).append(name)
    components = tuple(
        tuple(sorted(members, key=order.__getitem__)) for members in grouped.values()
    )
    return tuple(sorted(components, key=lambda members: (-len(members), min(members))))


def _require_shard_slot(shard: int, count: int) -> None:
    """Reject bools, non-integers, and indexes outside ``0 .. count-1``."""
    if isinstance(shard, bool) or isinstance(count, bool):
        msg = "Prepare shard index and count must be integers"
        raise TypeError(msg)
    if count < 1:
        msg = "Prepare shard count must be positive"
        raise ValueError(msg)
    if not 0 <= shard < count:
        msg = "Prepare shard index is outside its count"
        raise ValueError(msg)


def hosted_prepare_shard(raw_shard: str, raw_count: str) -> tuple[int, int] | None:
    """Parse hosted shard env, or return ``None`` when sharding is off.

    The count is the public macOS pool. A partial pair, a non-canonical
    integer, or any other count is a configuration error.
    """
    if raw_shard == "" and raw_count == "":
        return None
    if raw_shard == "" or raw_count == "":
        msg = "Prepare shard index and count must both be set"
        raise ValueError(msg)
    shard = _canonical_int(raw_shard)
    count = _canonical_int(raw_count)
    if shard is None or count is None:
        msg = "Prepare shard index and count must be integers"
        raise ValueError(msg)
    if count != DARWIN_PREPARE_SHARD_COUNT:
        msg = (
            "Prepare shard count must be "
            f"{DARWIN_PREPARE_SHARD_COUNT} for the public macOS pool"
        )
        raise ValueError(msg)
    if not 0 <= shard < count:
        msg = "Prepare shard index is outside the public macOS pool"
        raise ValueError(msg)
    return shard, count


def _canonical_int(raw: str) -> int | None:
    """Return *raw* as an integer when it is a canonical decimal form."""
    if not raw.isascii() or not raw.isdigit():
        return None
    value = int(raw)
    if str(value) != raw:
        return None
    return value


@dataclass(frozen=True)
class PrepareShardCoverage:
    """The full plan and the slice one runner hashed."""

    plan: tuple[str, ...]
    hashed: tuple[str, ...]
    shard: int
    count: int

    def to_json(self) -> str:
        """Serialize the sidecar that rides next to ``candidate.json``."""
        return (
            json.dumps(
                {
                    "plan": list(self.plan),
                    "hashed": list(self.hashed),
                    "shard": self.shard,
                    "count": self.count,
                },
                indent=2,
            )
            + "\n"
        )


def coverage_from_json(
    text: str,
    *,
    prerequisites: Mapping[str, Sequence[str]] | None = None,
) -> PrepareShardCoverage:
    """Load one sidecar and reject a slice that is not its component assignment."""
    record = parse_prepare_coverage(text)
    expected = partition_prepare_targets(
        record.plan,
        shard=record.shard,
        count=record.count,
        prerequisites=prerequisites,
    )
    if record.hashed != expected:
        msg = f"Prepare shard {record.shard} hashed a different slice"
        raise ValueError(msg)
    return record


def parse_prepare_coverage(text: str) -> PrepareShardCoverage:
    """Load one sidecar without checking which component slice it hashed."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        msg = "Prepare coverage must be JSON"
        raise ValueError(msg) from error
    if not isinstance(payload, dict) or set(payload) != _COVERAGE_KEYS:
        msg = "Prepare coverage must contain plan, hashed, shard, and count"
        raise ValueError(msg)
    shard = payload["shard"]
    count = payload["count"]
    if (
        isinstance(shard, bool)
        or isinstance(count, bool)
        or not isinstance(shard, int)
        or not isinstance(count, int)
    ):
        msg = "Prepare coverage shard and count must be integers"
        raise TypeError(msg)
    plan = _string_tuple(payload["plan"], "plan")
    hashed = _string_tuple(payload["hashed"], "hashed")
    _require_shard_slot(shard, count)
    return PrepareShardCoverage(plan=plan, hashed=hashed, shard=shard, count=count)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"Prepare coverage {field} must be a list of strings"
        raise ValueError(msg)
    return tuple(cast("list[str]", value))


def require_exact_prepare_coverage(
    records: tuple[PrepareShardCoverage, ...] | list[PrepareShardCoverage],
    *,
    prerequisites: Mapping[str, Sequence[str]] | None = None,
) -> tuple[str, ...]:
    """Return the shared plan when every name was hashed on its component shard.

    A prerequisite edge whose ends were hashed on different shards is rejected
    even when each sidecar would otherwise look populated. Every plan name is
    still hashed exactly once.
    """
    if not records:
        msg = "Prepare shard coverage is empty"
        raise ValueError(msg)
    edges = {} if prerequisites is None else prerequisites
    count = records[0].count
    plan = records[0].plan
    seen: dict[int, PrepareShardCoverage] = {}
    for record in records:
        if record.count != count:
            msg = "Prepare shard counts must match"
            raise ValueError(msg)
        if record.plan != plan:
            msg = "Prepare shard plans must match"
            raise ValueError(msg)
        if record.shard in seen:
            msg = f"Prepare shard {record.shard} is duplicated"
            raise ValueError(msg)
        seen[record.shard] = record
    if set(seen) != set(range(count)):
        msg = "Prepare shard coverage is missing a shard"
        raise ValueError(msg)
    owners = _hashed_owners(records)
    _require_no_cross_shard_edge(plan, owners, edges)
    for record in records:
        expected = partition_prepare_targets(
            plan,
            shard=record.shard,
            count=count,
            prerequisites=edges,
        )
        if record.hashed != expected:
            msg = f"Prepare shard {record.shard} hashed a different slice"
            raise ValueError(msg)
    return plan


def _hashed_owners(
    records: tuple[PrepareShardCoverage, ...] | list[PrepareShardCoverage],
) -> dict[str, int]:
    """Map each hashed name to the one shard that hashed it."""
    owners: dict[str, int] = {}
    for record in records:
        for name in record.hashed:
            if name in owners:
                msg = f"Prepare target {name} was hashed more than once"
                raise ValueError(msg)
            owners[name] = record.shard
    return owners


def _require_no_cross_shard_edge(
    plan: tuple[str, ...],
    owners: Mapping[str, int],
    prerequisites: Mapping[str, Sequence[str]],
) -> None:
    """Reject a consumer hashed apart from a prerequisite that is also in the plan."""
    selected = set(plan)
    for name in plan:
        for prerequisite in prerequisites.get(name, ()):
            if prerequisite not in selected:
                continue
            if name not in owners or prerequisite not in owners:
                continue
            if owners[name] != owners[prerequisite]:
                msg = (
                    "Prepare prerequisite edge crosses shards: "
                    f"{name} -> {prerequisite}"
                )
                raise ValueError(msg)
