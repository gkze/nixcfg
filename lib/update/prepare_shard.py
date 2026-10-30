"""Disjoint prepare slices for the public macOS runner pool.

Each shard keeps the requested target set. It hashes only
``index % count == shard`` of the deterministic plan order, and records that
slice. The same-platform merge accepts the shards only when those slices are
exactly the pure partition: every plan name once, none twice.
"""

import json
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
) -> tuple[str, ...]:
    """Return the plan names whose index belongs to *shard*."""
    _require_shard_slot(shard, count)
    if len(set(names)) != len(names):
        msg = "Prepare plan names must be unique"
        raise ValueError(msg)
    return tuple(name for index, name in enumerate(names) if index % count == shard)


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


def coverage_from_json(text: str) -> PrepareShardCoverage:
    """Load one sidecar and reject a slice that is not the pure partition."""
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
    expected = partition_prepare_targets(plan, shard=shard, count=count)
    if hashed != expected:
        msg = f"Prepare shard {shard} hashed a different slice"
        raise ValueError(msg)
    return PrepareShardCoverage(plan=plan, hashed=hashed, shard=shard, count=count)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"Prepare coverage {field} must be a list of strings"
        raise ValueError(msg)
    return tuple(cast("list[str]", value))


def require_exact_prepare_coverage(
    records: tuple[PrepareShardCoverage, ...] | list[PrepareShardCoverage],
) -> tuple[str, ...]:
    """Return the shared plan when every name was hashed by its own shard."""
    if not records:
        msg = "Prepare shard coverage is empty"
        raise ValueError(msg)
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
        expected = partition_prepare_targets(plan, shard=record.shard, count=count)
        if record.hashed != expected:
            msg = f"Prepare shard {record.shard} hashed a different slice"
            raise ValueError(msg)
        seen[record.shard] = record
    if set(seen) != set(range(count)):
        msg = "Prepare shard coverage is missing a shard"
        raise ValueError(msg)
    return plan
