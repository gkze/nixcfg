"""Per-target update summary and its final human/JSON rendering."""

import json
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from lib.diagnostics import redact_urls
from lib.update.outcomes import SummaryStatus, merge_statuses

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib.update.cli import OutputOptions


@dataclass
class UpdateSummary:
    """Final outcomes, with presentation lists derived from one ordered map."""

    statuses: dict[str, SummaryStatus] = field(default_factory=dict)

    @property
    def updated(self) -> list[str]:
        """Targets whose candidate changed."""
        return [name for name, status in self.statuses.items() if status == "updated"]

    @property
    def errors(self) -> list[str]:
        """Targets with an execution or validation failure."""
        return [name for name, status in self.statuses.items() if status == "error"]

    @property
    def dropped(self) -> list[str]:
        """Targets whose candidate was withheld because a coupled target failed."""
        return [name for name, status in self.statuses.items() if status == "dropped"]

    @property
    def no_change(self) -> list[str]:
        """Targets that completed without a candidate change."""
        return [name for name, status in self.statuses.items() if status == "no_change"]

    def to_dict(self) -> dict[str, list[str] | bool]:
        """Return the stable JSON summary projection."""
        return {
            "updated": [redact_urls(name) for name in self.updated],
            "dropped": [redact_urls(name) for name in self.dropped],
            "errors": [redact_urls(name) for name in self.errors],
            "noChange": [redact_urls(name) for name in self.no_change],
            "success": not self.errors,
        }

    def accumulate(self, details: dict[str, SummaryStatus]) -> None:
        """Merge outcomes without downgrading failures or changing target order."""
        self.statuses = merge_statuses(self.statuses, details)


def emit_summary(
    summary: UpdateSummary,
    *,
    had_errors: bool,
    out: OutputOptions,
    dry_run: bool,
    discarded_updates: tuple[str, ...] = (),
    indeterminate_updates: tuple[str, ...] = (),
    dropped: Mapping[str, str] | None = None,
) -> int:
    """Render the final per-target summary and return the run exit code."""
    dropped = dropped or {}
    if out.json_output:
        payload = dict[str, object](summary.to_dict())
        payload["success"] = not had_errors
        payload["withheld"] = {
            redact_urls(k): redact_urls(v) for k, v in dropped.items()
        }
        sys.stdout.write(f"{json.dumps(payload)}\n")
        return 1 if had_errors else 0

    if dry_run:
        if summary.updated:
            out.print(
                f"\nAvailable updates: {', '.join(summary.updated)}",
                style="green",
            )
        else:
            out.print("\nNo updates available.", style="dim")
    elif discarded_updates:
        out.print(
            f"\nCandidate updates discarded: {', '.join(discarded_updates)}",
            style="yellow",
        )
    elif indeterminate_updates:
        out.print(
            "\nCandidate updates have unknown promotion state: "
            f"{', '.join(indeterminate_updates)}",
            style="yellow",
        )
    elif summary.updated:
        out.print(
            f"\nUpdated: {', '.join(summary.updated)}",
            style="green",
        )
    elif had_errors:
        out.print("\nNo updates promoted.", style="yellow")
    else:
        out.print("\nNo updates needed.", style="dim")

    if dropped:
        rendered = ", ".join(f"{name} ({reason})" for name, reason in dropped.items())
        out.print(f"\nWithheld from promotion: {rendered}", style="yellow")

    if summary.errors:
        out.print_error(f"\nFailed: {', '.join(summary.errors)}")

    return 1 if had_errors else 0
