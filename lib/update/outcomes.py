"""Per-target outcomes shared by update execution and reporting."""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

type SummaryStatus = Literal["updated", "error", "no_change"]
_STATUS_PRIORITY = {"no_change": 0, "updated": 1, "error": 2}


def merge_statuses(
    current: Mapping[str, SummaryStatus],
    incoming: Mapping[str, SummaryStatus],
) -> dict[str, SummaryStatus]:
    """Preserve first-seen order while retaining the strongest observed outcome."""
    merged = dict(current)
    for name, status in incoming.items():
        merged[name] = max(
            merged.get(name, "no_change"),
            status,
            key=_STATUS_PRIORITY.__getitem__,
        )
    return merged
