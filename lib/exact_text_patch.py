"""Plan fail-closed, exact-text source patches without performing I/O.

The count-and-raise rule here is also implemented by
:func:`lib.codemods.text.replace_exactly`. Unsloth's patch script needs this
copy: its backend patch step runs under Python 3.12, while ``lib.codemods``
relies on Python 3.14 deferred annotations (``lib/codemods-pythonpath.nix``).
Keep the two implementations in step until Unsloth can use ``lib.codemods``.
"""

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
