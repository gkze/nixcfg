"""Plan fail-closed, exact-text source patches without performing I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExactTextPatch:
    """One exact source replacement and its reviewed occurrence count."""

    path: Path
    old: str
    new: str
    expected_count: int = 1


def plan_exact_text_patches(
    sources: Mapping[Path, str],
    patches: Iterable[ExactTextPatch],
    *,
    mismatch_message: Callable[[ExactTextPatch, int], str],
) -> dict[Path, str]:
    """Validate original and evolving sources, then return patched contents."""
    planned_patches = tuple(patches)
    for patch in planned_patches:
        count = sources[patch.path].count(patch.old)
        if count != patch.expected_count:
            raise RuntimeError(mismatch_message(patch, count))

    planned = dict(sources)
    for patch in planned_patches:
        source = planned[patch.path]
        count = source.count(patch.old)
        if count != patch.expected_count:
            raise RuntimeError(mismatch_message(patch, count))
        planned[patch.path] = source.replace(patch.old, patch.new)
    return planned
