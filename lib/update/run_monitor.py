"""Run-level progress state, heartbeat, and durable run log for update runs.

The monitor is the single owner of "what is happening right now" across the
asynchronous source phases and the synchronous validation phases. Any thread may
record events; readers take a consistent snapshot. SQLite holds structured
observations and heartbeat projections beside DBOS execution history.
``output.log`` remains a tail-able, redacted subprocess log. Execution status
comes from DBOS; a stale heartbeat never proves that a worker is still alive.
"""

import os
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TextIO

from pydantic import TypeAdapter

from lib.diagnostics import redact_urls
from lib.update.events import (
    CommandResult,
    StatusPayload,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.run_store import DATABASE_FILE, RunStore

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

type TargetOutcome = Literal["pending", "running", "updated", "no_change", "error"]
type Clock = Callable[[], float]
type WallClock = Callable[[], datetime]

OUTPUT_FILE = "output.log"
LATEST_LINK = "latest"
VALIDATION_TAIL_LINES = 8
_BUILDING_LINE = re.compile(r"building '/nix/store/[a-z0-9]{32}-(?P<name>[^']+)\.drv'")
_MAX_ACTIVITY_CHARS = 60


def format_duration(seconds: float) -> str:
    """Render a duration compactly for status lines."""
    total = max(0, int(seconds))
    if total < 60:  # noqa: PLR2004 -- unit boundary
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:  # noqa: PLR2004 -- unit boundary
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _truncate(text: str, limit: int = _MAX_ACTIVITY_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def default_run_log_root() -> Path:
    """Return the run log root under the XDG state directory."""
    raw_state_home = os.environ.get("XDG_STATE_HOME")
    state_home = (
        Path(raw_state_home).expanduser()
        if raw_state_home
        else Path.home() / ".local" / "state"
    )
    return state_home / "nixcfg" / "update" / "runs"


@dataclass(frozen=True)
class TargetActivity:
    """One running target as seen by the status line."""

    name: str
    activity: str
    running_seconds: float
    idle_seconds: float


@dataclass(frozen=True)
class ValidationActivity:
    """One synchronous validation command in flight."""

    label: str
    running_seconds: float
    idle_seconds: float
    builds_started: int
    current_build: str | None
    tail: tuple[str, ...]


@dataclass(frozen=True)
class RunStatus:
    """Immutable, JSON-safe projection of a run's progress."""

    run_id: str
    started_at: str
    elapsed_seconds: float
    phase: str | None
    phase_index: int
    phase_count: int
    total: int
    pending: int
    running: int
    updated: int
    no_change: int
    failed: int
    active: tuple[TargetActivity, ...]
    finished: bool
    validations: tuple[ValidationActivity, ...] = ()
    execution_status: str | None = None

    @property
    def done(self) -> int:
        """Return how many targets reached a terminal outcome."""
        return self.updated + self.no_change + self.failed


def format_status_line(
    status: RunStatus,
    *,
    inactivity_warning_seconds: float,
) -> str:
    """Render the one-line run summary shown in every output mode."""
    parts: list[str] = []
    if status.phase is not None:
        parts.append(f"Phase {status.phase_index}/{status.phase_count} {status.phase}")
    if status.execution_status is not None:
        parts.append(f"execution {status.execution_status.lower()}")
    elif status.finished:
        parts.append("finished")
    validations = status.validations
    if validations:
        # The status line spotlights the command started most recently; every
        # in-flight command is listed individually by ``--status``.
        validation = validations[-1]
        prefix = f"{len(validations)} running · " if len(validations) > 1 else ""
        step = prefix + (
            f"{_truncate(validation.label, 48)} "
            f"{format_duration(validation.running_seconds)}"
        )
        if validation.current_build is not None:
            step += f" · building {validation.current_build}"
        if validation.builds_started:
            step += f" ({validation.builds_started} started)"
        parts.append(step)
        idle = f"idle {format_duration(validation.idle_seconds)}"
        if validation.idle_seconds >= inactivity_warning_seconds:
            idle += " ⚠ no output"
        parts.append(idle)
    elif status.total:
        parts.append(f"{status.done}/{status.total} done")
        parts.append(f"{status.running} running")
        if status.failed:
            parts.append(f"{status.failed} failed")
        if status.active:
            longest = status.active[0]
            item = f"longest: {longest.name} {format_duration(longest.running_seconds)}"
            if longest.activity:
                item += f" ({longest.activity})"
            item += f", idle {format_duration(longest.idle_seconds)}"
            if longest.idle_seconds >= inactivity_warning_seconds:
                item += " ⚠"
            parts.append(item)
    parts.append(f"elapsed {format_duration(status.elapsed_seconds)}")
    return " · ".join(parts)


def format_run_status(status: RunStatus, *, updated_seconds_ago: float | None) -> str:
    """Render a multi-line human report for ``--status``."""
    lines = [
        f"Run {status.run_id} started {status.started_at}",
        format_status_line(status, inactivity_warning_seconds=float("inf")),
    ]
    if updated_seconds_ago is not None:
        lines.append(f"State written {format_duration(updated_seconds_ago)} ago")
    for target in status.active:
        line = (
            f"  {target.name}: {format_duration(target.running_seconds)} running, "
            f"idle {format_duration(target.idle_seconds)}"
        )
        if target.activity:
            line += f" — {target.activity}"
        lines.append(line)
    for validation_index, validation in enumerate(status.validations):
        if validation_index:
            lines.append(
                f"  {validation.label} · {format_duration(validation.running_seconds)}"
                f" running, idle {format_duration(validation.idle_seconds)}"
            )
        lines.extend(f"  > {tail_line}" for tail_line in validation.tail)
    return "\n".join(lines)


@dataclass
class _TargetState:
    outcome: TargetOutcome = "pending"
    started_at: float | None = None
    last_activity_at: float | None = None
    activity: str = ""
    inactivity_warned: bool = False


@dataclass
class _ValidationState:
    label: str
    started_at: float
    last_output_at: float
    builds_started: int = 0
    current_build: str | None = None
    tail: deque[str] = field(
        default_factory=lambda: deque(maxlen=VALIDATION_TAIL_LINES)
    )


class RunMonitor:
    """Thread-safe progress state with an optional durable run directory."""

    def __init__(
        self,
        *,
        targets: Sequence[str],
        phase_count: int,
        run_dir: Path | None = None,
        run_id: str | None = None,
        diagnostics: bool = True,
        inactivity_warning_seconds: float = 300.0,
        clock: Clock = time.monotonic,
        wall_clock: WallClock | None = None,
    ) -> None:
        """Create in-memory state and, when *run_dir* is given, its log files."""
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._started_at = clock()
        self._started_wall = self._wall_clock().isoformat(timespec="seconds")
        self.run_id = run_id or self._new_run_id()
        self.run_dir = run_dir
        self.inactivity_warning_seconds = inactivity_warning_seconds
        self._phase: str | None = None
        self._phase_index = 0
        self._phase_count = phase_count
        self._targets: dict[str, _TargetState] = {
            name: _TargetState() for name in targets
        }
        self._validations: dict[str, _ValidationState] = {}
        self._finished = False
        self._diagnostics = diagnostics
        self._store: RunStore | None = None
        self._output: TextIO | None = None
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            # Line buffering keeps subprocess output tail-able while the run is live.
            self._store = RunStore(run_dir)
            if diagnostics:
                self._output = (run_dir / OUTPUT_FILE).open(
                    "a", encoding="utf-8", buffering=1
                )
            self._store.write(
                "run",
                {
                    "runId": self.run_id,
                    "startedAt": self._started_wall,
                    "targets": list(targets),
                    "phaseCount": phase_count,
                },
            )
            self._point_latest(run_dir)
            self.write_state()

    @classmethod
    def start(
        cls,
        *,
        targets: Sequence[str],
        phase_count: int,
        run_root: Path | None,
        inactivity_warning_seconds: float,
    ) -> RunMonitor:
        """Create a monitor whose run directory lives under *run_root*."""
        run_id = cls._new_run_id()
        return cls(
            targets=targets,
            phase_count=phase_count,
            run_dir=None if run_root is None else run_root / run_id,
            run_id=run_id,
            inactivity_warning_seconds=inactivity_warning_seconds,
        )

    @staticmethod
    def _new_run_id() -> str:
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"

    @staticmethod
    def _point_latest(run_dir: Path) -> None:
        """Atomically repoint the ``latest`` link at *run_dir*."""
        link = run_dir.parent / LATEST_LINK
        temporary = run_dir.parent / f".{LATEST_LINK}.{secrets.token_hex(4)}"
        temporary.symlink_to(run_dir.name)
        try:
            temporary.replace(link)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    # -- recording -------------------------------------------------------

    def begin_phase(self, name: str, index: int) -> None:
        """Enter a numbered phase and log the transition."""
        with self._lock:
            self._phase = name
            self._phase_index = index
        self._write_event({"kind": "phase", "phase": name, "index": index})
        self.write_state()

    def note(self, message: str, **fields: object) -> None:
        """Log an informational, structured note for this run."""
        self._write_event({"kind": "note", "message": redact_urls(message), **fields})

    def record(self, event: UpdateEvent) -> None:
        """Fold one update event into progress state and the run log."""
        now = self._clock()
        kind = event.kind
        with self._lock:
            target = self._targets.get(event.source)
            if target is not None:
                self._apply(target, event, now)
        if kind is UpdateEventKind.LINE:
            label = (
                f"[{event.source}:{event.stream}]"
                if event.stream
                else f"[{event.source}]"
            )
            self._write_output(f"{label} {event.message or ''}")
            return
        record: dict[str, object] = {
            "kind": kind.value,
            "source": event.source,
        }
        if event.message is not None:
            record["message"] = event.message
        if event.detail is not None:
            record["detail"] = event.detail
        payload = event.payload
        if isinstance(payload, StatusPayload) and payload.operation is not None:
            record["operation"] = payload.operation
        if isinstance(payload, CommandResult):
            record["returncode"] = payload.returncode
            self._write_output(
                f"[{event.source}] exit {payload.returncode}: {' '.join(payload.args)}"
            )
        elif kind is UpdateEventKind.COMMAND_START and event.message:
            self._write_output(f"[{event.source}] $ {event.message}")
        self._write_event(record)

    @staticmethod
    def _apply(target: _TargetState, event: UpdateEvent, now: float) -> None:
        kind = event.kind
        if target.started_at is None:
            target.started_at = now
        target.last_activity_at = now
        if target.outcome == "pending":
            target.outcome = "running"
        if kind is UpdateEventKind.RESULT:
            if target.outcome != "error":
                target.outcome = "updated" if event.payload is not None else "no_change"
        elif kind is UpdateEventKind.ERROR:
            target.outcome = "error"
            target.activity = _truncate((event.message or "").splitlines()[0])
        elif kind in {UpdateEventKind.STATUS, UpdateEventKind.COMMAND_START}:
            target.activity = _truncate(event.message or "")

    def validation_started(self, label: str) -> None:
        """Track one synchronous validation command from its start."""
        now = self._clock()
        with self._lock:
            # Re-starting a retried command resets its state and moves it to
            # the most recent position.
            self._validations.pop(label, None)
            self._validations[label] = _ValidationState(
                label=redact_urls(label), started_at=now, last_output_at=now
            )
        self._write_event({"kind": "validation_start", "label": redact_urls(label)})
        self._write_output(f"[validation] $ {redact_urls(label)}")

    def validation_output(self, command: str | None, line: str) -> None:
        """Record one output line, attributed to *command* when known.

        Unattributed lines fold into the most recently started command, which
        matches the sequential execution most notes belong to; concurrent
        commands emit attributed lines through ``ValidationCommandOutput``.
        """
        line = redact_urls(line.rstrip("\r\n"))
        now = self._clock()
        with self._lock:
            if command is not None:
                state = self._validations.get(command)
            else:
                # ``reversed`` over the dict yields labels in most-recent-first
                # insertion order.
                most_recent = next(reversed(self._validations), None)
                state = (
                    self._validations[most_recent] if most_recent is not None else None
                )
            if state is not None:
                state.last_output_at = now
                state.tail.append(_truncate(line, 160))
                if match := _BUILDING_LINE.search(line):
                    state.builds_started += 1
                    state.current_build = match.group("name")
        self._write_output(f"[validation] {line}")

    def validation_finished(self, command: str, *, succeeded: bool) -> None:
        """Clear one in-flight validation command and log its outcome."""
        with self._lock:
            self._validations.pop(command, None)
        self._write_event({
            "kind": "validation_end",
            "label": redact_urls(command),
            "succeeded": succeeded,
        })

    # -- reading ---------------------------------------------------------

    def snapshot(self) -> RunStatus:
        """Return a consistent view of the run for rendering or persistence."""
        now = self._clock()
        with self._lock:
            counts = {
                "pending": 0,
                "running": 0,
                "updated": 0,
                "no_change": 0,
                "error": 0,
            }
            active: list[TargetActivity] = []
            for name, target in self._targets.items():
                counts[target.outcome] += 1
                if target.outcome == "running":
                    started = (
                        target.started_at if target.started_at is not None else now
                    )
                    last = (
                        target.last_activity_at
                        if target.last_activity_at is not None
                        else started
                    )
                    active.append(
                        TargetActivity(
                            name=name,
                            activity=target.activity,
                            running_seconds=now - started,
                            idle_seconds=now - last,
                        )
                    )
            active.sort(key=lambda item: (-item.running_seconds, item.name))
            validations = tuple(
                ValidationActivity(
                    label=state.label,
                    running_seconds=now - state.started_at,
                    idle_seconds=now - state.last_output_at,
                    builds_started=state.builds_started,
                    current_build=state.current_build,
                    tail=tuple(state.tail),
                )
                for state in self._validations.values()
            )
            return RunStatus(
                run_id=self.run_id,
                started_at=self._started_wall,
                elapsed_seconds=now - self._started_at,
                phase=self._phase,
                phase_index=self._phase_index,
                phase_count=self._phase_count,
                total=len(self._targets),
                pending=counts["pending"],
                running=counts["running"],
                updated=counts["updated"],
                no_change=counts["no_change"],
                failed=counts["error"],
                active=tuple(active),
                validations=validations,
                finished=self._finished,
            )

    def status_line(self) -> str:
        """Render the current status line."""
        return format_status_line(
            self.snapshot(),
            inactivity_warning_seconds=self.inactivity_warning_seconds,
        )

    def newly_stalled(self) -> tuple[TargetActivity, ...]:
        """Return running targets that just crossed the inactivity threshold."""
        now = self._clock()
        stalled: list[TargetActivity] = []
        with self._lock:
            for name, target in self._targets.items():
                if target.outcome != "running" or target.inactivity_warned:
                    continue
                last = target.last_activity_at
                if last is None or now - last < self.inactivity_warning_seconds:
                    continue
                target.inactivity_warned = True
                started = target.started_at if target.started_at is not None else last
                stalled.append(
                    TargetActivity(
                        name=name,
                        activity=target.activity,
                        running_seconds=now - started,
                        idle_seconds=now - last,
                    )
                )
        return tuple(stalled)

    # -- persistence -----------------------------------------------------

    def write_state(self) -> None:
        """Persist the latest snapshot for ``--status`` and crash inspection."""
        if self._store is None:
            return
        payload = asdict(self.snapshot())
        payload["updatedAt"] = self._wall_clock().isoformat(timespec="seconds")
        self._store.write("status", payload)

    def _write_event(self, record: dict[str, object]) -> None:
        with self._lock:
            if self._store is not None and self._diagnostics:
                self._store.append({
                    "t": self._wall_clock().isoformat(timespec="milliseconds"),
                    **record,
                })

    def _write_output(self, line: str) -> None:
        with self._lock:
            stream = self._output
            if stream is not None:
                stream.write(line + "\n")

    # -- lifecycle -------------------------------------------------------

    def start_heartbeat(
        self,
        interval: float,
        printer: Callable[[str], None] | None,
    ) -> None:
        """Refresh the SQLite status projection periodically and optionally print status lines."""
        if self._heartbeat is not None:
            msg = "Heartbeat already started"
            raise RuntimeError(msg)
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(interval, printer),
            name="update-heartbeat",
            daemon=True,
        )
        self._heartbeat.start()

    def _heartbeat_loop(
        self,
        interval: float,
        printer: Callable[[str], None] | None,
    ) -> None:
        while not self._stop.wait(interval):
            self.heartbeat_tick(printer)

    def heartbeat_tick(self, printer: Callable[[str], None] | None) -> None:
        """Perform one heartbeat: persist state and surface stalled targets."""
        self.write_state()
        if printer is None:
            return
        printer(self.status_line())
        for target in self.newly_stalled():
            printer(
                f"[{target.name}] no output for "
                f"{format_duration(target.idle_seconds)}"
                + (f"; last activity: {target.activity}" if target.activity else "")
            )

    def close(self, summary: Mapping[str, object] | None = None) -> None:
        """Stop the heartbeat, record the final state, and close log files."""
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join()
            self._heartbeat = None
        with self._lock:
            self._finished = True
        if summary is not None:
            self._write_event({"kind": "summary", **dict(summary)})
        if self._store is not None:
            run_payload = TypeAdapter(dict[str, object]).validate_python(
                self._store.read("run")
            )
            run_payload["finishedAt"] = self._wall_clock().isoformat(timespec="seconds")
            if summary is not None:
                run_payload["summary"] = dict(summary)
            self._store.write("run", run_payload)
        self.write_state()
        with self._lock:
            if self._output is not None:
                self._output.close()
            self._store = None
            self._output = None


def load_run_status(
    run_root: Path,
    run_id: str | None = None,
) -> tuple[Path, RunStatus, str | None]:
    """Load the persisted status of one run, defaulting to the latest.

    Stored state is a trust transition: it is validated against the status
    dataclasses rather than assumed to match the writer's schema.
    """
    run_dir = run_root / (run_id or LATEST_LINK)
    if not (run_dir / DATABASE_FILE).is_file():
        msg = f"No recorded update run at {run_dir}"
        raise FileNotFoundError(msg)
    payload = TypeAdapter(dict[str, object]).validate_python(
        RunStore(run_dir, readonly=True).read("status")
    )
    updated_at = payload.pop("updatedAt", None)
    status = TypeAdapter(RunStatus).validate_python(payload)
    execution = RunStore(run_dir, readonly=True).execution_status()
    if execution is not None:
        status = replace(
            status,
            execution_status=execution,
            finished=execution in {"SUCCESS", "ERROR"},
        )
    return run_dir.resolve(), status, None if updated_at is None else str(updated_at)


def seconds_since(
    timestamp: str | None, *, now: datetime | None = None
) -> float | None:
    """Return the age of an ISO timestamp, or ``None`` when unknown."""
    if timestamp is None:
        return None
    moment = datetime.fromisoformat(timestamp)
    current = now if now is not None else datetime.now(moment.tzinfo)
    return max(0.0, (current - moment).total_seconds())


def target_names_for(*groups: Iterable[str]) -> tuple[str, ...]:
    """Merge target groups in first-seen order for monitor construction."""
    return tuple(dict.fromkeys(name for group in groups for name in group))


__all__ = [
    "LATEST_LINK",
    "OUTPUT_FILE",
    "RunMonitor",
    "RunStatus",
    "TargetActivity",
    "ValidationActivity",
    "default_run_log_root",
    "format_duration",
    "format_run_status",
    "format_status_line",
    "load_run_status",
    "seconds_since",
    "target_names_for",
]
