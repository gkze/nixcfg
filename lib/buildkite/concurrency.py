"""Admit one build the way Actions concurrency admits one workflow run.

Buildkite step concurrency would also serialize matrix cells, so admission
runs in the bootstrap step before the graph is uploaded. Default-branch
Update waits. A concurrency group with ``cancel-in-progress`` cancels older
builds of that group.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_ACTIVE = frozenset({
    "scheduled",
    "running",
    "failing",
    "blocked",
    "canceling",
    "waiting",
    "assigned",
    "accepted",
})


@dataclass(frozen=True)
class BuildRef:
    """One pipeline build the admission check can see."""

    build_id: str
    number: int
    state: str


@dataclass(frozen=True)
class Admission:
    """What the bootstrap step should do before uploading the graph."""

    cancel_ids: tuple[str, ...]
    wait: bool


def admit(builds: Sequence[BuildRef], *, current_id: str, cancel: bool) -> Admission:
    """Return the cancel set or a wait for builds that are still active.

    Only older builds are cancelled. A newer build is left alone so two
    bootstraps cannot cancel each other.
    """
    try:
        current = next(build for build in builds if build.build_id == current_id)
    except StopIteration:
        current = None
    others = [
        build
        for build in builds
        if build.build_id != current_id and build.state in _ACTIVE
    ]
    if not others:
        return Admission((), wait=False)
    if not cancel:
        return Admission((), wait=True)
    if current is None:
        return Admission(tuple(build.build_id for build in others), wait=False)
    older = tuple(build.build_id for build in others if build.number < current.number)
    newer = any(build.number > current.number for build in others)
    return Admission(older, newer)
