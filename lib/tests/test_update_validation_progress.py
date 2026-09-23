"""Validation progress must remain live without changing process ownership."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from io import BufferedRandom
from pathlib import Path
from typing import cast

import pytest

from lib.tests._run_updates_helpers import make_run_plan
from lib.update import derivation_validation as validation
from lib.update.cli import (
    OutputOptions,
    UpdateOptions,
    _RunOutcome,
    _update_cancellation_check,
    _validate_round,
    _ValidationContext,
)
from lib.update.config import resolve_config
from lib.update.persistence import UpdateValidationSnapshot
from lib.update.run_monitor import EVENTS_FILE, RunMonitor


def _assert_reader_stopped() -> None:
    assert not any(
        thread.name.startswith("validation-output") for thread in threading.enumerate()
    )


@pytest.mark.parametrize("returncode", [0, 1])
def test_validation_streams_before_exit_and_retains_both_outputs(
    tmp_path: Path, returncode: int
) -> None:
    """The child cannot complete until its first output has reached the observer."""
    gate = tmp_path / "observed"
    script = """
import pathlib, sys, time
gate = pathlib.Path(sys.argv[1])
print("first", flush=True)
print("diagnostic", file=sys.stderr, flush=True)
deadline = time.monotonic() + 5
while not gate.exists():
    if time.monotonic() > deadline:
        raise RuntimeError("output was not streamed")
    time.sleep(0.01)
sys.stdout.buffer.write("last ü".encode())
sys.stdout.flush()
raise SystemExit(int(sys.argv[2]))
"""
    events: list[validation.ValidationProgressEvent] = []

    def progress(event: validation.ValidationProgressEvent) -> None:
        events.append(event)
        if (
            isinstance(event, validation.ValidationCommandOutput)
            and event.line == "first"
        ):
            gate.touch()

    args = [sys.executable, "-c", script, str(gate), str(returncode)]
    result = validation._run_validation_command(
        args,
        cwd=tmp_path,
        timeout=10,
        run=None,
        sleep=lambda _: pytest.fail("deterministic outcomes must not retry"),
        progress=progress,
    )

    assert result.returncode == returncode
    assert result.stdout == "first\nlast ü"
    assert result.stderr == "diagnostic\n"
    lines = [
        event.line
        for event in events
        if isinstance(event, validation.ValidationCommandOutput)
    ]
    assert lines[0] == "first"
    assert set(lines) == {"first", "diagnostic", "last ü"}
    assert isinstance(events[0], validation.ValidationCommandStarted)
    assert events[-1] == validation.ValidationCommandFinished(
        events[0].command, returncode == 0
    )
    _assert_reader_stopped()


def test_validation_timeout_reaps_child_and_drains_progress(tmp_path: Path) -> None:
    """A timed out real child and its output reader both finish before return."""
    seen: list[str] = []

    def progress(event: validation.ValidationProgressEvent) -> None:
        if isinstance(event, validation.ValidationCommandOutput):
            seen.append(event.line)

    with pytest.raises(subprocess.TimeoutExpired):
        validation._run_with_validation_progress(
            [
                sys.executable,
                "-c",
                "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
            ],
            cwd=tmp_path,
            timeout=0.3,
            run=None,
            progress=progress,
        )
    assert len(seen) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(seen[0]), 0)
    _assert_reader_stopped()


def test_validation_without_progress_retains_failed_output(tmp_path: Path) -> None:
    """Quiet capture uses the owned process without creating an output reader."""
    result = validation._run_validation_command(
        [
            sys.executable,
            "-c",
            "import sys; print('output'); print('assembly invalid',file=sys.stderr); sys.exit(1)",
        ],
        cwd=tmp_path,
        timeout=5,
        run=None,
        sleep=lambda _: pytest.fail("deterministic outcomes must not retry"),
    )
    assert result.returncode == 1
    assert result.stdout == "output\n"
    assert result.stderr == "assembly invalid\n"
    _assert_reader_stopped()


def test_validation_interrupt_reaps_child_and_reader() -> None:
    """Deliver a real SIGINT in an isolated interpreter, leaving pytest untouched."""
    script = """
import json, os, pathlib, signal, subprocess, sys, threading
from lib.update.derivation_validation import (
    ValidationCommandOutput,
    _run_with_validation_progress,
)
seen = []
def progress(event):
    if isinstance(event, ValidationCommandOutput):
        seen.append(event.line)
        os.kill(os.getpid(), signal.SIGINT)
try:
    _run_with_validation_progress(
        [sys.executable, "-c", "import os,time; print(os.getpid(),flush=True); time.sleep(30)"],
        cwd=pathlib.Path.cwd(), timeout=10, run=None, progress=progress,
    )
except KeyboardInterrupt:
    pass
else:
    raise AssertionError("SIGINT must propagate")
assert len(seen) == 1
try:
    os.kill(int(seen[0]), 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("child escaped validation")
assert not any(t.name.startswith("validation-output") for t in threading.enumerate())
print(json.dumps({"reaped": True}))
"""
    result = subprocess.run(  # noqa: S603 -- fixed local Python test program
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {"reaped": True}


@pytest.mark.parametrize("mode", ["verbose", "normal", "quiet", "json"])
def test_update_async_host_single_sigint_reaps_before_snapshot_or_promotion(
    tmp_path: Path, mode: str
) -> None:
    """One SIGINT must cancel the real async owner while sync validation is waiting."""
    script = """
import asyncio, json, os, pathlib, signal, sys, threading, time
from contextlib import contextmanager
from lib.update import cli, derivation_validation as validation
from lib.update.persistence import UpdateValidationSnapshot
root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
pidfile = root / "child.pid"
state = {"promoted": False, "after_validation": False, "snapshot_closed": False}

class Workspace:
    def __init__(self, root): self.root = root
    def __enter__(self): return self
    def __exit__(self, *_): pass
    @contextmanager
    def validation_snapshot(self):
        try:
            yield UpdateValidationSnapshot(root=root, changed_paths=())
        finally:
            child_pid = int(pidfile.read_text())
            try: os.kill(child_pid, 0)
            except ProcessLookupError: pass
            else: raise AssertionError("child escaped snapshot")
            state["snapshot_closed"] = True
    def promote(self, *_): state["promoted"] = True

def roots(**kwargs):
    child = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); print('building',flush=True); time.sleep(30)"
    validation._run_validation_command(
        [sys.executable, "-c", child, str(pidfile)], cwd=root,
        timeout=10, run=None, sleep=lambda _: None,
        progress=kwargs["progress"], check_cancelled=kwargs["check_cancelled"],
    )
    state["after_validation"] = True
    return ()

def interrupt():
    deadline = time.monotonic() + 5
    while not pidfile.exists():
        if time.monotonic() >= deadline: raise AssertionError("child never started")
        time.sleep(0.01)
    os.kill(os.getpid(), signal.SIGINT)

cli.update_persistence.IsolatedUpdateWorkspace = Workspace
cli._handle_preflight_requests = lambda *_: None
cli._revalidate_runtime_source_snapshot = lambda *_: None
cli._build_run_plan = lambda *_: None
cli._requires_root_closure_validation = lambda *_: True
validation.validate_root_closures = roots
sender = threading.Thread(target=interrupt)
sender.start()
started = time.monotonic()
try:
    asyncio.run(cli.run_updates(cli.UpdateOptions(
        verbose=mode == "verbose", quiet=mode == "quiet", json=mode == "json", tty="off"
    )))
except KeyboardInterrupt:
    state["interrupted"] = True
else:
    raise AssertionError("single SIGINT was not honored")
sender.join()
assert time.monotonic() - started < 2
assert not any(t.name.startswith("validation-output") for t in threading.enumerate())
print(json.dumps(state))
"""
    result = subprocess.run(  # noqa: S603 -- fixed local Python test program
        [sys.executable, "-c", script, str(tmp_path), mode],
        cwd=Path(__file__).parents[2],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "promoted": False,
        "after_validation": False,
        "snapshot_closed": True,
        "interrupted": True,
    }


def test_validation_cancellation_checker_rejects_the_owning_task() -> None:
    """The synchronous checkpoint sees cancellation requested by asyncio.Runner."""

    async def run() -> None:
        check = _update_cancellation_check()
        check()
        task = asyncio.current_task()
        assert task is not None
        task.cancel()
        check()
        pytest.fail("a cancelled task must not continue validation")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_validation_progress_reader_failure_is_propagated(tmp_path: Path) -> None:
    """A failed observer promptly kills a long-running child and joins the reader."""
    seen: list[str] = []

    def broken_progress(event: validation.ValidationProgressEvent) -> None:
        if isinstance(event, validation.ValidationCommandOutput):
            seen.append(event.line)
        raise BrokenPipeError("observer failed")

    started = time.monotonic()
    with pytest.raises(BrokenPipeError, match="observer failed"):
        validation._run_with_validation_progress(
            [
                sys.executable,
                "-c",
                "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
            ],
            cwd=tmp_path,
            timeout=None,
            run=None,
            progress=broken_progress,
        )
    assert time.monotonic() - started < 2
    assert len(seen) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(int(seen[0]), 0)
    _assert_reader_stopped()


def test_validation_progress_preserves_retry_diagnostics(tmp_path: Path) -> None:
    """Streamed error capture still drives the existing transient-only retry rule."""
    marker = tmp_path / "attempt"
    script = """
import pathlib, sys
marker = pathlib.Path(sys.argv[1])
if not marker.exists():
    marker.touch()
    print("error: unable to download 'https://cache.nixos.org/demo.narinfo': connection reset by peer", file=sys.stderr)
    raise SystemExit(1)
print("complete")
"""
    seen: list[validation.ValidationProgressEvent] = []
    sleeps: list[float] = []
    result = validation._run_validation_command(
        [sys.executable, "-c", script, str(marker)],
        cwd=tmp_path,
        timeout=5,
        run=None,
        sleep=sleeps.append,
        progress=seen.append,
    )
    assert result.returncode == 0
    assert result.stdout == "complete\n"
    assert sleeps == [1.0]
    assert "Retrying transient Nix failure (attempt 2/3)" in seen
    assert any(
        isinstance(event, validation.ValidationCommandFinished) and event.succeeded
        for event in seen
    )
    _assert_reader_stopped()


def test_root_validation_streams_manifest_and_failed_batch_fallback(
    tmp_path: Path,
) -> None:
    """The public root validator retains manifest parsing and individual failures."""
    calls: list[list[str]] = []
    manifest = {
        "schemaVersion": 2,
        "requiredKinds": ["darwin", "home"],
        "requiredRoots": [],
        "roots": [
            {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
            {"kind": "home", "name": "george", "system": "aarch64-linux"},
        ],
    }

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        stdout = kwargs["stdout"]
        stderr = kwargs["stderr"]
        assert isinstance(stdout, BufferedRandom)
        assert isinstance(stderr, BufferedRandom)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 17
        if args[1] == "eval":
            stdout.write(json.dumps(manifest).encode())
            stdout.flush()
            return subprocess.CompletedProcess(args, 0)
        assert "-L" in args
        assert "--no-link" in args
        stderr.write(b"builder diagnostic\n")
        stderr.flush()
        return subprocess.CompletedProcess(
            args, 1 if "aarch64-linux" in args[-1] else 0
        )

    seen: list[validation.ValidationProgressEvent] = []
    failures = validation.validate_root_closures(
        flake_root=tmp_path,
        timeout=17,
        run=run,
        progress=seen.append,
        sleep=lambda _: pytest.fail("deterministic outcomes must not retry"),
        print_build_logs=True,
    )
    assert len(calls) == 4
    assert calls[0][1] == "eval"
    assert "-L" not in calls[0]
    assert len(failures) == 1
    assert failures[0].installable == "path:.#checks.aarch64-linux.root-closures"
    assert failures[0].message == "builder diagnostic"
    assert "Batch validation did not succeed; isolating failing targets" in seen
    assert (
        sum(
            isinstance(event, validation.ValidationCommandOutput)
            and event.line == "builder diagnostic"
            for event in seen
        )
        == 3
    )
    _assert_reader_stopped()


@pytest.mark.parametrize(
    ("verbose", "quiet", "json_output"),
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
def test_validation_phase_output_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    verbose: bool,
    quiet: bool,
    json_output: bool,
) -> None:
    """Normal users see phase headings; only verbose humans see subprocess lines."""
    calls: list[str] = []

    def validate(*_args: object, **kwargs: object) -> tuple[object, ...]:
        progress = kwargs["progress"]
        if callable(progress):
            emit = cast("validation.ValidationProgress", progress)
            emit(validation.ValidationCommandStarted("nix build -L [literal]"))
            emit("\x1b[31mbuilding dependency\x1b[0m")
            emit("https://example.test/archive?signature=fixture-signature")
        calls.append("validate")
        return ()

    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(validation, "validate_derivations", validate)
    monkeypatch.setattr(validation, "validate_root_closures", validate)
    monkeypatch.setattr(
        "lib.update.cli._requires_root_closure_validation", lambda *_: True
    )
    assert _validate_round(
        make_run_plan(source_names=("demo",)),
        UpdateValidationSnapshot(root=tmp_path, changed_paths=()),
        _RunOutcome(),
        _ValidationContext(
            opts=UpdateOptions(
                verbose=verbose, quiet=quiet, json=json_output, tty="off"
            ),
            out=OutputOptions(quiet=quiet, json_output=json_output),
            config=resolve_config(),
            updaters={},
            check_cancelled=lambda: None,
        ),
        round_index=0,
    ) == (False, False)
    captured = capsys.readouterr()
    assert calls == ["validate", "validate"]
    assert "fixture-signature" not in captured.out
    if quiet or json_output:
        assert captured.out == ""
        assert captured.err == ""
    else:
        assert "Phase 3: derivation validation" in captured.out
        assert "Phase 4: root closure builds" in captured.out
        assert ("$ nix build -L [literal]" in captured.out) == verbose
        assert ("building dependency" in captured.out) == verbose
        assert ("https://example.test/archive?REDACTED" in captured.out) == verbose
        assert "\x1b" not in captured.out


def test_validation_round_reports_phases_to_the_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Validation rounds feed phase and validation events to the run monitor."""
    monitor = RunMonitor(targets=("demo",), phase_count=4)

    def validate(*_args: object, **kwargs: object) -> tuple[object, ...]:
        progress = kwargs["progress"]
        if callable(progress):
            emit = cast("validation.ValidationProgress", progress)
            emit(validation.ValidationCommandStarted("nix build -L [literal]"))
        return ()

    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(validation, "validate_derivations", validate)
    monkeypatch.setattr(validation, "validate_root_closures", validate)
    monkeypatch.setattr(
        "lib.update.cli._requires_root_closure_validation", lambda *_: True
    )
    assert _validate_round(
        make_run_plan(source_names=("demo",)),
        UpdateValidationSnapshot(root=tmp_path, changed_paths=()),
        _RunOutcome(),
        _ValidationContext(
            opts=UpdateOptions(tty="off"),
            out=OutputOptions(),
            config=resolve_config(),
            updaters={},
            check_cancelled=lambda: None,
            monitor=monitor,
        ),
        round_index=1,
    ) == (False, False)
    monitor.close()
    status = monitor.snapshot()
    assert status.phase == "root closures"
    assert (status.phase_index, status.phase_count) == (4, 4)


def test_validation_round_records_events_for_the_run_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """A run-log-backed monitor records validation events durably."""
    monitor = RunMonitor.start(
        targets=("demo",),
        phase_count=4,
        run_root=tmp_path_factory.mktemp("runs"),
        inactivity_warning_seconds=300.0,
    )

    def validate(*_args: object, **kwargs: object) -> tuple[object, ...]:
        progress = kwargs["progress"]
        if callable(progress):
            emit = cast("validation.ValidationProgress", progress)
            emit(validation.ValidationCommandStarted("nix build -L roots"))
            emit(
                validation.ValidationCommandFinished(
                    "nix build -L roots", succeeded=True
                )
            )
        return ()

    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(validation, "validate_derivations", validate)
    monkeypatch.setattr(validation, "validate_root_closures", validate)
    monkeypatch.setattr(
        "lib.update.cli._requires_root_closure_validation", lambda *_: True
    )
    assert _validate_round(
        make_run_plan(source_names=("demo",)),
        UpdateValidationSnapshot(root=tmp_path, changed_paths=()),
        _RunOutcome(),
        _ValidationContext(
            opts=UpdateOptions(tty="off"),
            out=OutputOptions(),
            config=resolve_config(),
            updaters={},
            check_cancelled=lambda: None,
            monitor=monitor,
        ),
        round_index=0,
    ) == (False, False)
    monitor.close()
    assert monitor.run_dir is not None
    events = (monitor.run_dir / EVENTS_FILE).read_text(encoding="utf-8")
    assert '"phase": "derivation validation"' in events
    assert '"phase": "root closures"' in events
    assert '"succeeded": true' in events
