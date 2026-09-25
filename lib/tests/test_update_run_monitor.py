"""Behavior tests for run-level progress state, heartbeat, and the run log."""

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from lib.update.events import (
    CommandResult,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
)
from lib.update.run_monitor import (
    LATEST_LINK,
    OUTPUT_FILE,
    RunMonitor,
    RunStatus,
    TargetActivity,
    ValidationActivity,
    default_run_log_root,
    format_duration,
    format_run_status,
    format_status_line,
    load_run_status,
    seconds_since,
    target_names_for,
)
from lib.update.run_store import DATABASE_FILE, RunStore

RUN_ID = "20260913-190800-ab12"
WALL = datetime(2026, 9, 13, 19, 8, tzinfo=UTC)
STALL_SECONDS = 60.0


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _monitor(
    run_dir: Path | None,
    *,
    targets: tuple[str, ...] = ("alpha", "beta"),
) -> tuple[RunMonitor, _Clock]:
    clock = _Clock()
    monitor = RunMonitor(
        targets=targets,
        phase_count=4,
        run_dir=run_dir,
        run_id=RUN_ID,
        inactivity_warning_seconds=STALL_SECONDS,
        clock=clock,
        wall_clock=lambda: WALL,
    )
    return monitor, clock


def _status(**overrides: object) -> RunStatus:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "started_at": WALL.isoformat(),
        "elapsed_seconds": 754.0,
        "phase": "sources",
        "phase_index": 2,
        "phase_count": 4,
        "total": 131,
        "pending": 88,
        "running": 6,
        "updated": 30,
        "no_change": 5,
        "failed": 2,
        "active": (),
        "validations": (),
        "finished": False,
    }
    base.update(overrides)
    return RunStatus(**base)  # type: ignore[arg-type]


def test_format_duration_units() -> None:
    """Durations render in the largest useful unit and never go negative."""
    assert format_duration(5) == "5s"
    assert format_duration(65) == "1m05s"
    assert format_duration(3725) == "1h02m"
    assert format_duration(-3) == "0s"


def test_diagnostics_disabled_retains_status_without_output_or_events(
    tmp_path: Path,
) -> None:
    """Disabling optional logs does not disable the operational status projection."""
    monitor = RunMonitor(
        targets=("alpha",),
        phase_count=4,
        run_dir=tmp_path,
        run_id=RUN_ID,
        diagnostics=False,
    )
    monitor.record(UpdateEvent.result("alpha"))
    monitor.close()
    monitor.write_state()
    store = RunStore(tmp_path, readonly=True)
    assert store.events() == []
    assert store.read("status")["finished"]
    assert not (tmp_path / OUTPUT_FILE).exists()


def test_execution_status_is_distinct_from_finished_projection() -> None:
    """A stale final heartbeat must not conceal a still-pending workflow."""
    line = format_status_line(
        _status(execution_status="PENDING", finished=True),
        inactivity_warning_seconds=60,
    )
    assert "execution pending" in line
    assert "finished" not in line


def test_status_line_reports_counts_and_longest_running() -> None:
    """The status line names the phase, counts, and the slowest target."""
    longest = TargetActivity(
        name="gitbutler",
        activity="Refreshing crate2nix artifacts...",
        running_seconds=552.0,
        idle_seconds=182.0,
    )
    line = format_status_line(
        _status(active=(longest,)), inactivity_warning_seconds=STALL_SECONDS
    )
    assert line == (
        "Phase 2/4 sources · 37/131 done · 6 running · 2 failed · "
        "longest: gitbutler 9m12s (Refreshing crate2nix artifacts...), "
        "idle 3m02s ⚠ · elapsed 12m34s"
    )

    quiet = format_status_line(
        _status(active=(), failed=0, updated=0, no_change=0, pending=131, running=0),
        inactivity_warning_seconds=STALL_SECONDS,
    )
    assert quiet == "Phase 2/4 sources · 0/131 done · 0 running · elapsed 12m34s"

    unlabeled = TargetActivity(
        name="zo", activity="", running_seconds=3.0, idle_seconds=1.0
    )
    assert "longest: zo 3s, idle 1s ·" in format_status_line(
        _status(active=(unlabeled,)), inactivity_warning_seconds=STALL_SECONDS
    )

    empty = format_status_line(
        _status(phase=None, total=0, finished=True),
        inactivity_warning_seconds=STALL_SECONDS,
    )
    assert empty == "finished · elapsed 12m34s"


def test_status_line_describes_validation_steps() -> None:
    """During validation the line shows the command, current build, and idle time."""
    validation = ValidationActivity(
        label="derivations: nix build path:/tmp/x#checks.aarch64-darwin.root-closures",
        running_seconds=95.0,
        idle_seconds=70.0,
        builds_started=3,
        current_build="foo-1.2",
        tail=("copying path", "building foo"),
    )
    line = format_status_line(
        _status(phase="root closures", phase_index=4, validations=(validation,)),
        inactivity_warning_seconds=STALL_SECONDS,
    )
    assert line.startswith("Phase 4/4 root closures · derivations: nix build path:")
    assert "…" in line
    assert "1m35s · building foo-1.2 (3 started) · idle 1m10s ⚠ no output" in line

    fresh = ValidationActivity(
        label="derivations: nix eval",
        running_seconds=2.0,
        idle_seconds=0.0,
        builds_started=0,
        current_build=None,
        tail=(),
    )
    assert (
        format_status_line(
            _status(validations=(fresh,)), inactivity_warning_seconds=STALL_SECONDS
        )
        == "Phase 2/4 sources · derivations: nix eval 2s · idle 0s · elapsed 12m34s"
    )


def test_format_run_status_lists_active_targets_and_tail() -> None:
    """The status report explains each running target and recent output."""
    report = format_run_status(
        _status(
            active=(
                TargetActivity(
                    name="mux",
                    activity="Computing hash",
                    running_seconds=61,
                    idle_seconds=2,
                ),
                TargetActivity(
                    name="zo", activity="", running_seconds=5, idle_seconds=5
                ),
            ),
            validations=(
                ValidationActivity(
                    label="roots",
                    running_seconds=10,
                    idle_seconds=1,
                    builds_started=0,
                    current_build=None,
                    tail=("line one",),
                ),
                ValidationActivity(
                    label="derivations",
                    running_seconds=20,
                    idle_seconds=3,
                    builds_started=1,
                    current_build=None,
                    tail=("line two",),
                ),
            ),
        ),
        updated_seconds_ago=42.0,
    )
    assert report.splitlines() == [
        f"Run {RUN_ID} started {WALL.isoformat()}",
        "Phase 2/4 sources · 2 running · derivations 20s (1 started) · idle 3s · elapsed 12m34s",
        "State written 42s ago",
        "  mux: 1m01s running, idle 2s — Computing hash",
        "  zo: 5s running, idle 5s",
        "  > line one",
        "  derivations · 20s running, idle 3s",
        "  > line two",
    ]
    assert "State written" not in format_run_status(_status(), updated_seconds_ago=None)


def test_record_tracks_outcomes_activity_and_ordering(tmp_path: Path) -> None:
    """Events drive per-target outcome, activity text, and idle accounting."""
    monitor, clock = _monitor(None)
    assert monitor.snapshot().pending == 2

    monitor.record(
        UpdateEvent.status(
            "alpha",
            "Fetching latest alpha version...",
            operation="check_version",
            status=StatusInfo(kind=StatusKind.LATEST_VERSION, value="1.0"),
        )
    )
    clock.advance(10)
    monitor.record(
        UpdateEvent(
            source="beta",
            kind=UpdateEventKind.COMMAND_START,
            message="nix build -L --no-link path:.#beta",
            payload=["nix", "build"],
        )
    )
    clock.advance(5)
    monitor.record(
        UpdateEvent(
            source="beta",
            kind=UpdateEventKind.LINE,
            message="building",
            stream="stderr",
        )
    )
    monitor.record(UpdateEvent.status("ghost", "ignored source"))

    status = monitor.snapshot()
    assert (status.pending, status.running, status.done) == (0, 2, 0)
    assert [item.name for item in status.active] == ["alpha", "beta"]
    alpha, beta = status.active
    assert alpha.activity == "Fetching latest alpha version..."
    assert (alpha.running_seconds, alpha.idle_seconds) == (15.0, 15.0)
    assert beta.activity == "nix build -L --no-link path:.#beta"
    assert (beta.running_seconds, beta.idle_seconds) == (5.0, 0.0)

    monitor.record(
        UpdateEvent(
            source="beta",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["nix", "build"], returncode=0, stdout="", stderr=""
            ),
        )
    )
    monitor.record(UpdateEvent.result("beta", payload="entry"))
    monitor.record(UpdateEvent.error("alpha", "boom\ntraceback detail", detail="tb"))
    monitor.record(UpdateEvent.result("alpha"))
    status = monitor.snapshot()
    assert (status.updated, status.failed, status.no_change, status.running) == (
        1,
        1,
        0,
        0,
    )
    assert status.active == ()
    assert status.done == 2

    monitor.record(UpdateEvent.status("beta", ""))
    assert monitor.snapshot().updated == 1

    solo, _ = _monitor(None, targets=("gamma",))
    solo.record(UpdateEvent.result("gamma"))
    assert solo.snapshot().no_change == 1
    assert "0/1 done" not in solo.status_line()


def test_run_directory_files_and_latest_link(tmp_path: Path) -> None:
    """A run directory records its plan, events, output, state, and a latest link."""
    root = tmp_path / "runs"
    monitor, _clock = _monitor(root / RUN_ID)
    monitor.begin_phase("sources", 2)
    monitor.note("withheld", failed=["beta"])
    monitor.record(
        UpdateEvent(
            source="alpha",
            kind=UpdateEventKind.COMMAND_START,
            message="git fetch https://user:pw@example.test/repo",
            payload=["git", "fetch"],
        )
    )
    monitor.record(UpdateEvent(source="alpha", kind=UpdateEventKind.LINE, message="ok"))
    monitor.record(
        UpdateEvent(
            source="alpha",
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=["git", "fetch"], returncode=1, stdout="", stderr=""
            ),
        )
    )
    monitor.record(UpdateEvent.error("alpha", "failed", detail="Traceback ..."))

    run_dir = root / RUN_ID
    run = RunStore(run_dir, readonly=True).read("run")
    assert run == {
        "runId": RUN_ID,
        "startedAt": WALL.isoformat(),
        "targets": ["alpha", "beta"],
        "phaseCount": 4,
    }
    events = RunStore(run_dir, readonly=True).events()
    assert [event["kind"] for event in events] == [
        "phase",
        "note",
        "command_start",
        "command_end",
        "error",
    ]
    assert events[1] == {
        "t": events[1]["t"],
        "kind": "note",
        "message": "withheld",
        "failed": ["beta"],
    }
    assert events[3]["returncode"] == 1
    assert events[4]["detail"] == "Traceback ..."
    assert "pw" not in json.dumps(RunStore(run_dir, readonly=True).events())
    output = (run_dir / OUTPUT_FILE).read_text(encoding="utf-8").splitlines()
    assert output[0].startswith("[alpha] $ git fetch https://")
    assert output[1:] == ["[alpha] ok", "[alpha] exit 1: git fetch"]
    state = RunStore(run_dir, readonly=True).read("status")
    assert state["phase"] == "sources"
    assert state["updatedAt"] == WALL.isoformat()
    assert (root / LATEST_LINK).resolve() == run_dir.resolve()

    later, _ = _monitor(root / "20260913-200000-cd34")
    assert (root / LATEST_LINK).resolve() == (root / "20260913-200000-cd34").resolve()
    later.close()
    monitor.close()


def test_latest_link_failure_leaves_no_temporary_link(tmp_path: Path) -> None:
    """A latest pointer that cannot be replaced fails loudly and cleanly."""
    root = tmp_path / "runs"
    (root / LATEST_LINK).mkdir(parents=True)
    (root / LATEST_LINK / "occupied").write_text("x", encoding="utf-8")
    with pytest.raises(IsADirectoryError):
        _monitor(root / RUN_ID)
    assert [
        path.name for path in root.iterdir() if path.name.startswith(".latest")
    ] == []


def test_validation_tracking_parses_builds_and_bounds_the_tail(tmp_path: Path) -> None:
    """Validation output feeds the tail, build detection, and idle accounting."""
    root = tmp_path / "runs"
    monitor, clock = _monitor(root / RUN_ID)
    monitor.validation_output(None, "ignored before any command")
    command = "derivations: nix build path:/x#a https://u:p@host/q?s=1"
    monitor.validation_started(command)
    clock.advance(3)
    for index in range(10):
        monitor.validation_output(command, f"line {index}")
    monitor.validation_output(
        command,
        "building '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-foo-1.2.drv'...",
    )
    monitor.validation_output(command, "x" * 200)

    validation = monitor.snapshot().validations[-1]
    assert validation.label.startswith("derivations: nix build path:/x#a https://")
    assert "u:p@" not in validation.label
    assert validation.running_seconds == 3.0
    assert validation.idle_seconds == 0.0
    assert validation.builds_started == 1
    assert validation.current_build == "foo-1.2"
    assert len(validation.tail) == 8
    assert validation.tail[-1].endswith("…")
    assert "Phase" not in monitor.status_line()
    assert "building foo-1.2 (1 started)" in monitor.status_line()

    clock.advance(STALL_SECONDS)
    assert "⚠ no output" in monitor.status_line()

    monitor.validation_finished(command, succeeded=False)
    assert monitor.snapshot().validations == ()
    monitor.validation_finished(command, succeeded=True)
    events = RunStore(root / RUN_ID, readonly=True).events()
    assert [(event["kind"], event.get("succeeded")) for event in events] == [
        ("validation_start", None),
        ("validation_end", False),
        ("validation_end", True),
    ]
    assert events[2]["label"] == events[0]["label"]
    output = (root / RUN_ID / OUTPUT_FILE).read_text(encoding="utf-8")
    assert output.startswith("[validation] ignored before any command\n[validation] $ ")
    monitor.close()


def test_concurrent_validation_commands_track_independently(tmp_path: Path) -> None:
    """Concurrent validation commands keep separate clocks, tails, and endings."""
    root = tmp_path / "runs"
    monitor, clock = _monitor(root / RUN_ID)
    monitor.validation_started("derivations: eval a")
    monitor.validation_started("derivations: eval b")
    monitor.validation_output("derivations: eval a", "a output")
    monitor.validation_output(
        "derivations: eval b",
        "building '/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-b-1.0.drv'...",
    )
    clock.advance(2)

    snapshot = monitor.snapshot()
    assert [item.label for item in snapshot.validations] == [
        "derivations: eval a",
        "derivations: eval b",
    ]
    assert snapshot.validations[0].tail == ("a output",)
    assert snapshot.validations[0].running_seconds == 2.0
    assert snapshot.validations[0].idle_seconds == 2.0
    assert snapshot.validations[1].builds_started == 1
    assert snapshot.validations[1].current_build == "b-1.0"
    assert "2 running · derivations: eval b" in monitor.status_line()

    monitor.validation_finished("derivations: eval a", succeeded=True)
    snapshot = monitor.snapshot()
    assert [item.label for item in snapshot.validations] == ["derivations: eval b"]
    monitor.validation_finished("derivations: eval b", succeeded=False)
    assert monitor.snapshot().validations == ()
    monitor.close()


def test_heartbeat_tick_prints_status_and_stall_warnings_once(tmp_path: Path) -> None:
    """Each tick persists state, prints the status line, and warns per stall."""
    root = tmp_path / "runs"
    monitor, clock = _monitor(root / RUN_ID)
    monitor.begin_phase("sources", 2)
    printed: list[str] = []

    monitor.heartbeat_tick(None)
    assert printed == []
    assert (root / RUN_ID / DATABASE_FILE).exists()

    monitor.record(UpdateEvent.status("alpha", "Fetching hashes"))
    monitor.record(UpdateEvent.status("beta", "Starting"))
    monitor.record(UpdateEvent.result("beta"))
    assert monitor.newly_stalled() == ()
    clock.advance(STALL_SECONDS)
    monitor.heartbeat_tick(printed.append)
    assert printed[0].startswith("Phase")
    assert printed[1] == "[alpha] no output for 1m00s; last activity: Fetching hashes"
    monitor.heartbeat_tick(printed.append)
    assert len(printed) == 3
    monitor.close()

    silent, silent_clock = _monitor(None, targets=("gamma",))
    silent.record(UpdateEvent.status("gamma", ""))
    silent_clock.advance(STALL_SECONDS)
    lines: list[str] = []
    silent.heartbeat_tick(lines.append)
    assert lines[1] == "[gamma] no output for 1m00s"


def test_heartbeat_thread_runs_until_close(tmp_path: Path) -> None:
    """The heartbeat thread ticks on its interval and stops when the run closes."""
    monitor, _clock = _monitor(tmp_path / RUN_ID)
    seen = threading.Event()
    printed: list[str] = []

    def printer(line: str) -> None:
        printed.append(line)
        seen.set()

    monitor.start_heartbeat(0.01, printer)
    with pytest.raises(RuntimeError, match="already started"):
        monitor.start_heartbeat(0.01, printer)
    assert seen.wait(5)
    monitor.close(summary={"updated": ["alpha"]})
    assert printed
    assert monitor.snapshot().finished
    run = RunStore(tmp_path / RUN_ID, readonly=True).read("run")
    assert run["finishedAt"] == WALL.isoformat()
    assert run["summary"] == {"updated": ["alpha"]}
    events = json.dumps(RunStore(tmp_path / RUN_ID, readonly=True).events())
    assert '"kind": "summary"' in events
    state = RunStore(tmp_path / RUN_ID, readonly=True).read("status")
    assert state["finished"] is True

    # After close, recording is inert and does not reopen the files.
    monitor.record(UpdateEvent.status("alpha", "late"))
    monitor.note("late")
    assert '"late"' not in json.dumps(
        RunStore(tmp_path / RUN_ID, readonly=True).events()
    )


def test_in_memory_monitor_has_no_files(tmp_path: Path) -> None:
    """Without a run directory the monitor still tracks progress."""
    monitor, _clock = _monitor(None)
    monitor.begin_phase("flake input refs", 1)
    monitor.write_state()
    monitor.close(summary={})
    assert monitor.run_dir is None
    assert list(tmp_path.iterdir()) == []
    assert (
        monitor.status_line()
        == "Phase 1/4 flake input refs · finished · 0/2 done · 0 running · elapsed 0s"
    )


def test_load_run_status_roundtrip_latest_and_named(tmp_path: Path) -> None:
    """Persisted state loads back as a status object with its freshness stamp."""
    root = tmp_path / "runs"
    monitor, clock = _monitor(root / RUN_ID)
    monitor.record(UpdateEvent.status("alpha", "Fetching"))
    clock.advance(2)
    monitor.validation_started("roots: nix build")
    monitor.validation_output("roots: nix build", "building")
    monitor.write_state()

    run_dir, status, updated_at = load_run_status(root)
    assert run_dir == (root / RUN_ID).resolve()
    assert updated_at == WALL.isoformat()
    assert status == monitor.snapshot()
    assert status.active[0].name == "alpha"
    assert status.validations[-1].tail == ("building",)

    monitor.validation_finished("roots: nix build", succeeded=True)
    monitor.close()
    _, named, _ = load_run_status(root, RUN_ID)
    assert named.finished
    assert named.validations == ()

    with pytest.raises(FileNotFoundError, match="No recorded update run at"):
        load_run_status(root, "missing")


def test_seconds_since_and_target_names_for() -> None:
    """Freshness math tolerates unknown stamps; target lists merge in order."""
    assert seconds_since(None) is None
    later = WALL + timedelta(seconds=90)
    assert seconds_since(WALL.isoformat(), now=later) == 90.0
    assert seconds_since(later.isoformat(), now=WALL) == 0.0
    assert seconds_since(WALL.isoformat()) is not None
    assert target_names_for(["b", "a"], ("a", "c"), ()) == ("b", "a", "c")


def test_default_run_log_root_respects_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run log lives under XDG_STATE_HOME when set, else under ~/.local/state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_run_log_root() == tmp_path / "state" / "nixcfg" / "update" / "runs"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_run_log_root() == (
        tmp_path / "home" / ".local" / "state" / "nixcfg" / "update" / "runs"
    )


def test_start_creates_a_fresh_run_id_and_directory(tmp_path: Path) -> None:
    """``start`` mints a timestamped run id and nests it under the root."""
    monitor = RunMonitor.start(
        targets=("alpha",),
        phase_count=4,
        run_root=tmp_path / "runs",
        inactivity_warning_seconds=1.0,
    )
    assert monitor.run_dir == tmp_path / "runs" / monitor.run_id
    assert monitor.run_dir.is_dir()
    assert len(monitor.run_id.split("-")) == 3
    monitor.close()

    memory_only = RunMonitor.start(
        targets=(), phase_count=4, run_root=None, inactivity_warning_seconds=1.0
    )
    assert memory_only.run_dir is None
    memory_only.close()


def test_load_run_status_rejects_malformed_state(tmp_path: Path) -> None:
    """A state file that is not a status object fails closed."""
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    RunStore(run_dir).write("status", [])
    with pytest.raises(ValidationError, match="valid dictionary"):
        load_run_status(tmp_path / "runs", RUN_ID)

    monitor, _clock = _monitor(None)
    payload = json.loads(json.dumps(monitor.snapshot().__dict__, default=list))
    RunStore(run_dir).write("status", payload)
    _, status, updated_at = load_run_status(tmp_path / "runs", RUN_ID)
    assert updated_at is None
    assert status.run_id == RUN_ID
