"""Shared profiling helpers for CI cache/build commands."""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, cast

from lib.update.ci._time import format_duration

if TYPE_CHECKING:
    from pathlib import Path

    from lib.nix.commands.base import ProcessLine

log = logging.getLogger(__name__)

_INTERNAL_JSON_PREFIX = "@nix "
_DRV_ACTIVITY_START_RE = re.compile(
    r"^(?:building|checking outputs of) '(/nix/store/[a-z0-9]{32}-[^']+\.drv)'(?:\.\.\.)?$"
)

_format_duration = format_duration


@dataclass(frozen=True)
class BuildProfileEvent:
    """Timed derivation activity extracted from Nix internal-json logs."""

    derivation: str
    duration_seconds: float
    completed: bool


class AggregatedProfileRow(TypedDict):
    """Aggregated per-derivation build timing row."""

    derivation: str
    name: str
    seconds: float
    occurrences: int
    completed: bool


class BuildProfiler:
    """Collect per-derivation timings from ``--log-format internal-json`` output."""

    def __init__(self) -> None:
        """Initialise in-flight and completed build timing storage."""
        self._active: dict[int, tuple[str, float]] = {}
        self.events: list[BuildProfileEvent] = []

    def ingest_line(self, line: str, *, now: float) -> None:
        """Consume one raw log line and update in-flight/completed timings."""
        event = parse_internal_json_line(line)
        if event is None:
            return

        action = event.get("action")
        if action == "start":
            event_id = event.get("id")
            text = event.get("text")
            if not isinstance(event_id, int) or not isinstance(text, str):
                return
            match = _DRV_ACTIVITY_START_RE.match(text)
            if match is None:
                return
            self._active[event_id] = (match.group(1), now)
            return

        if action != "stop":
            return

        event_id = event.get("id")
        if not isinstance(event_id, int):
            return
        in_flight = self._active.pop(event_id, None)
        if in_flight is None:
            return
        derivation, started = in_flight
        self.events.append(
            BuildProfileEvent(
                derivation=derivation,
                duration_seconds=max(0.0, now - started),
                completed=True,
            )
        )

    def finalize(self, *, now: float) -> None:
        """Mark in-flight derivation builds as interrupted."""
        for derivation, started in self._active.values():
            self.events.append(
                BuildProfileEvent(
                    derivation=derivation,
                    duration_seconds=max(0.0, now - started),
                    completed=False,
                )
            )
        self._active.clear()


def parse_internal_json_line(line: str) -> dict[str, object] | None:
    """Parse one internal-json line, returning ``None`` for non-JSON lines."""
    if not line.startswith(_INTERNAL_JSON_PREFIX):
        return None
    payload = line[len(_INTERNAL_JSON_PREFIX) :].strip()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def emit_stream_line(line: ProcessLine) -> None:
    """Forward streamed process output to the corresponding terminal stream."""
    stream = sys.stdout if line.stream == "stdout" else sys.stderr
    stream.write(line.text)
    stream.flush()


def _derivation_display_name(drv_path: str) -> str:
    """Convert ``/nix/store/<hash>-name.drv`` to ``name`` for summaries."""
    filename = drv_path.rsplit("/", maxsplit=1)[-1]
    _, _, name = filename.partition("-")
    return name.removesuffix(".drv") if name else filename


def aggregate_profile_events(
    events: list[BuildProfileEvent],
) -> list[AggregatedProfileRow]:
    """Aggregate per-event timings by derivation path."""
    by_drv: dict[str, AggregatedProfileRow] = {}
    for event in events:
        entry = by_drv.get(event.derivation)
        if entry is None:
            new_entry = cast(
                "AggregatedProfileRow",
                {
                    "derivation": event.derivation,
                    "name": _derivation_display_name(event.derivation),
                    "seconds": 0.0,
                    "occurrences": 0,
                    "completed": True,
                },
            )
            by_drv[event.derivation] = new_entry
            entry = new_entry
        entry["seconds"] += event.duration_seconds
        entry["occurrences"] += 1
        entry["completed"] = entry["completed"] and event.completed

    rows = list(by_drv.values())
    rows.sort(key=lambda row: row["seconds"], reverse=True)
    for row in rows:
        row["seconds"] = round(row["seconds"], 3)
    return rows


def write_profile_report(
    *,
    output_path: Path,
    flake_refs: list[str],
    derivation_count: int,
    profiler: BuildProfiler,
) -> None:
    """Persist profiling data for CI artifact upload and local inspection."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    aggregated = aggregate_profile_events(profiler.events)
    completed_count = sum(1 for row in aggregated if bool(row["completed"]))
    interrupted_count = len(aggregated) - completed_count
    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "flake_refs": flake_refs,
        "requested_derivations": derivation_count,
        "profiled_derivations": len(aggregated),
        "completed_derivations": completed_count,
        "interrupted_derivations": interrupted_count,
        "total_profiled_seconds": round(
            sum(row["seconds"] for row in aggregated),
            3,
        ),
        "slowest_derivations": aggregated[:100],
        "all_derivations": aggregated,
    }
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def log_profile_summary(
    events: list[AggregatedProfileRow],
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Print a compact slowest-build summary to CI logs."""
    active_log = log if logger is None else logger

    if not events:
        active_log.info("No derivation build events captured for profiling.")
        return

    completed = sum(1 for row in events if bool(row["completed"]))
    interrupted = len(events) - completed
    active_log.info(
        "Profiled %d derivation(s): %d completed, %d interrupted",
        len(events),
        completed,
        interrupted,
    )
    for row in events[:10]:
        status = "done" if bool(row["completed"]) else "interrupted"
        active_log.info(
            "  %s (%s) - %s",
            row["name"],
            status,
            _format_duration(row["seconds"]),
        )
