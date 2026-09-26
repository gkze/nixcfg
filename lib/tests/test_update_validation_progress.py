"""Validation progress must remain live without changing process ownership."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from io import BufferedRandom
from pathlib import Path
from typing import cast

import pytest

from lib.tests._run_updates_helpers import (
    configure_isolated_run,
    drain_events,
    make_run_plan,
)
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update import cli, source_runner
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
from lib.update.persistence import IsolatedUpdateWorkspace, UpdateValidationSnapshot
from lib.update.refs import FlakeInputRef
from lib.update.run_monitor import RunMonitor
from lib.update.run_store import RunStore
from lib.update.runtime import runtime_scope
from lib.update.updaters import Updater


@pytest.mark.parametrize("validate_all_packages", [False, True])
@pytest.mark.parametrize("has_plan", [False, True])
def test_repaired_retry_validates_held_packages_without_retargeting(
    monkeypatch, tmp_path, validate_all_packages, has_plan
) -> None:
    """Repair-only baseline failure must block admission, including no-op retries."""

    class Held(Updater):
        bulk_update_hold = "Keep the pinned release"
        derivation_validations = (
            validation.DerivationValidation(installable=".#held", mode="build"),
        )

    checked = []

    def validate_requests(requests, **_kwargs):
        checked.extend(requests)
        return tuple(
            validation.DerivationValidationFailure(
                request.source, request.installable, "broken repair"
            )
            for request in requests
        )

    monkeypatch.setattr(validation, "validate_derivation_requests", validate_requests)
    monkeypatch.setattr(
        validation, "get_current_nix_platform", lambda: "aarch64-darwin"
    )
    outcome = _RunOutcome()
    plan = make_run_plan(source_names=("demo",)) if has_plan else None
    result = _validate_round(
        plan,
        UpdateValidationSnapshot(root=tmp_path, changed_paths=()),
        outcome,
        _ValidationContext(
            opts=UpdateOptions(validate_all_packages=validate_all_packages),
            out=OutputOptions(quiet=True),
            config=resolve_config(),
            updaters={"held": Held},
            check_cancelled=lambda: None,
        ),
        round_index=0,
    )
    assert result == (validate_all_packages, False)
    assert [request.source for request in checked] == (
        ["held"] if validate_all_packages else []
    )
    assert bool(outcome.summary.errors) == validate_all_packages
    if plan is not None:
        assert tuple(plan.order) == ("demo",)


@pytest.mark.parametrize("validate_all_packages", [False, True])
@pytest.mark.parametrize("flake_only", [False, True])
@pytest.mark.parametrize("failure", [None, "package", "root"])
def test_repair_baseline_noop_runs_inventory_and_roots_before_promotion(
    tmp_path, monkeypatch, validate_all_packages, flake_only, failure
) -> None:
    """Real workspace/gates must validate repair bytes already in the baseline."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/held/default.nix": "# repaired packaging\n",
            "flake.nix": "# repaired flake\n",
        },
    )

    class Held(Updater):
        bulk_update_hold = "Keep the pinned release"
        derivation_validations = (
            validation.DerivationValidation(installable=".#held", mode="build"),
        )

    plan = (
        make_run_plan(
            ref_inputs=(FlakeInputRef("demo", "owner", "repo", "v1", "github"),)
        )
        if flake_only
        else None
    )
    events = []
    original_snapshot = IsolatedUpdateWorkspace.validation_snapshot
    original_promote = IsolatedUpdateWorkspace.promote

    @contextmanager
    def snapshot(workspace):
        with original_snapshot(workspace) as captured:
            assert captured.changed_paths == ()
            yield captured

    def promote(workspace, paths):
        events.append("promote")
        return original_promote(workspace, paths)

    def packages(requests, **kwargs):
        if not requests:
            return ()
        assert [request.source for request in requests] == ["held"]
        assert (
            kwargs["flake_root"] / "packages/held/default.nix"
        ).read_text() == "# repaired packaging\n"
        events.append("package")
        return (
            (validation.DerivationValidationFailure("held", ".#held", "broken repair"),)
            if failure == "package"
            else ()
        )

    def roots(**kwargs):
        assert (kwargs["flake_root"] / "flake.nix").read_text() == "# repaired flake\n"
        events.append("root")
        return (
            (validation.DerivationValidationFailure("root", ".#root", "broken repair"),)
            if failure == "root"
            else ()
        )

    async def refs(**_kwargs):
        return source_runner.UpdatePhaseResult(details={"demo": "no_change"})

    async def unexpected_sources(*_args, **_kwargs):
        pytest.fail("Broad validation must not update held sources")

    monkeypatch.setattr(cli, "get_repo_root", lambda: root)
    monkeypatch.setattr(cli, "_build_run_plan", lambda _opts: plan)
    monkeypatch.setattr(cli, "_get_updaters", lambda: {"held": Held})
    monkeypatch.setattr(cli, "consume_events", drain_events)
    monkeypatch.setattr(source_runner, "run_ref_phase", refs)
    monkeypatch.setattr(source_runner, "run_sources_phase", unexpected_sources)
    monkeypatch.setattr(validation, "validate_derivation_requests", packages)
    monkeypatch.setattr(validation, "validate_root_closures", roots)
    monkeypatch.setattr(
        validation, "get_current_nix_platform", lambda: "aarch64-darwin"
    )
    monkeypatch.setattr(IsolatedUpdateWorkspace, "validation_snapshot", snapshot)
    monkeypatch.setattr(IsolatedUpdateWorkspace, "promote", promote)
    status = asyncio.run(
        cli.run_updates(
            UpdateOptions(
                validate_all_packages=validate_all_packages,
                no_sources=True,
                json=True,
            )
        )
    )
    assert status == int(validate_all_packages and failure is not None)
    if not validate_all_packages:
        assert events == ["promote"]
    elif failure == "package":
        assert events == ["package"]
    elif failure == "root":
        assert events == ["package", "root"]
    else:
        assert events == ["package", "root", "promote"]


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


@pytest.mark.parametrize("observe", [False, True])
def test_validation_timeout_reaps_child_and_drains_progress(
    tmp_path: Path, observe: bool
) -> None:
    """A timed out real child and its output reader both finish before return."""
    seen: list[str] = []

    def progress(event: validation.ValidationProgressEvent) -> None:
        if isinstance(event, validation.ValidationCommandOutput):
            seen.append(event.line)

    events = []

    def observe_event(event):
        events.append(event)
        progress(event)

    with pytest.raises(validation.ValidationIncompleteError) as raised:
        validation._run_validation_command(
            [
                sys.executable,
                "-c",
                "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
            ],
            cwd=tmp_path,
            timeout=0.3,
            run=None,
            sleep=lambda _: pytest.fail("timeout must not retry"),
            progress=observe_event if observe else None,
        )
    assert raised.value.__context__ is None
    pid = str(raised.value).split("stdout:\n", 1)[1].strip()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)
    if observe:
        assert len(seen) == 1
        assert isinstance(events[0], validation.ValidationCommandStarted)
        assert events[-1] == validation.ValidationCommandFinished(
            events[0].command, succeeded=False
        )
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
    lifecycle = [
        event
        for event in seen
        if isinstance(
            event,
            (validation.ValidationCommandStarted, validation.ValidationCommandFinished),
        )
    ]
    assert len(lifecycle) == 4
    assert isinstance(lifecycle[0], validation.ValidationCommandStarted)
    assert lifecycle[1] == validation.ValidationCommandFinished(
        lifecycle[0].command, succeeded=False
    )
    assert lifecycle[2] == lifecycle[0]
    assert lifecycle[3] == validation.ValidationCommandFinished(
        lifecycle[0].command, succeeded=True
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
            emit(
                validation.ValidationCommandOutput(
                    "nix build -L [literal]", "build output"
                )
            )
            emit(
                validation.ValidationCommandFinished(
                    "nix build -L [literal]", succeeded=True
                )
            )
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
            emit(validation.ValidationCommandOutput("nix build -L [literal]", "output"))
            emit("plain validation progress")
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
    events = RunStore(monitor.run_dir, readonly=True).events()
    assert any(event.get("phase") == "derivation validation" for event in events)
    assert any(event.get("phase") == "root closures" for event in events)
    assert any(event.get("succeeded") is True for event in events)


@pytest.mark.parametrize("phase", ["packages", "roots"])
@pytest.mark.parametrize("full_scope", [False, True])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("run_logging", [False, True])
def test_incomplete_local_validation_discards_candidate_without_withholding(
    monkeypatch, tmp_path, capsys, phase, full_scope, json_output, run_logging
) -> None:
    """The real snapshot/promotion owner fails the run, never the selected package."""
    root = tmp_path / "repo"
    monkeypatch.setenv("UPDATE_RUN_LOG", "1" if run_logging else "0")
    run_root = tmp_path / "runs"
    monkeypatch.setenv("UPDATE_RUN_LOG_DIR", str(run_root))
    init_update_workspace_repo(root, tracked_files={".root": "", "flake.lock": "old"})

    async def execute(*_args, **_kwargs):
        Path("flake.lock").write_text("candidate")
        outcome = cli._RunOutcome(
            candidate_updates=("demo",),
            written_paths=(Path.cwd() / "flake.lock",),
        )
        outcome.summary.accumulate({"demo": "updated"})
        return outcome

    configure_isolated_run(
        monkeypatch,
        root=root,
        plan=make_run_plan(source_names=("demo",)),
        execute_result=execute,
        planned_paths=("flake.lock",),
        updaters={},
    )

    def incomplete(*_args, **_kwargs):
        raise validation.ValidationIncompleteError(
            "Validation incomplete: command deadline"
        )

    monkeypatch.setattr(
        validation,
        "validate_derivations",
        incomplete if phase == "packages" else lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(validation, "validate_root_closures", incomplete)
    monkeypatch.setattr(
        cli,
        "_withhold_failed_clusters",
        lambda *_args: pytest.fail("incomplete validation cannot withhold"),
    )
    assert (
        asyncio.run(
            cli.run_updates(
                UpdateOptions(
                    json=json_output,
                    validate_all_packages=full_scope,
                )
            )
        )
        == 1
    )
    assert (root / "flake.lock").read_text() == "old"
    if run_logging:
        retained = RunStore(run_root / "latest", readonly=True).read("run")
        assert retained["summary"]["validationIncomplete"] == (
            "Validation incomplete: command deadline"
        )
        assert (
            "Validation incomplete: command deadline"
            in (run_root / "latest" / "output.log").read_text()
        )
    else:
        assert not (run_root / "latest").exists()
    output = capsys.readouterr()
    if json_output:
        result = json.loads(output.out)
        assert result["success"] is False
        assert result["errors"] == ["validation"]
        assert result["withheld"] == {}
        assert result["updated"] == []
        assert result["candidateUpdatesDiscarded"] == ["demo"]
        assert "command deadline" in result["validationIncomplete"]
    else:
        assert "Validation incomplete: command deadline" in output.err


@pytest.mark.parametrize(
    "error", [OSError("cannot launch"), KeyboardInterrupt(), asyncio.CancelledError()]
)
def test_validation_exception_closes_progress(tmp_path, error) -> None:
    """Launch errors and cancellation close progress without changing their ownership."""
    events = []

    def run(*_args, **_kwargs):
        raise error

    expected = (
        validation.ValidationIncompleteError
        if isinstance(error, OSError)
        else type(error)
    )
    with pytest.raises(expected):
        validation._run_validation_command(
            ["nix", "build", ".#demo"],
            cwd=tmp_path,
            timeout=1,
            run=run,
            sleep=lambda _: pytest.fail("exception must not retry"),
            progress=events.append,
        )
    assert len(events) == 2
    assert isinstance(events[0], validation.ValidationCommandStarted)
    assert events[1] == validation.ValidationCommandFinished(
        events[0].command, succeeded=False
    )


@pytest.mark.parametrize("capture", ["streams", "exception"])
@pytest.mark.parametrize("large", [False, True])
def test_timeout_diagnostics_are_retained_bounded_and_counted_once(
    tmp_path, capture, large
) -> None:
    """Both runner contracts retain each stream's useful tail and one failure."""
    prefix = "old output\n" * 10000 if large else ""
    stdout = (prefix + "stdout tail").encode()
    stderr = prefix + "stderr tail"
    events = []

    def run(args, **kwargs):
        if capture == "streams":
            kwargs["stdout"].write(stdout)
            kwargs["stderr"].write(stderr.encode())
            raise subprocess.TimeoutExpired(args, 1)
        raise subprocess.TimeoutExpired(args, 1, output=stdout, stderr=stderr)

    async def exercise():
        async with runtime_scope(resolve_config()) as runtime:
            with pytest.raises(validation.ValidationIncompleteError) as raised:
                validation._run_validation_command(
                    ["nix", "build", ".#demo"],
                    cwd=tmp_path,
                    timeout=1,
                    run=run,
                    sleep=lambda _: pytest.fail("incomplete execution must not retry"),
                    progress=events.append,
                )
            timing = runtime.timing("validation", "build")
            assert (timing.count, timing.failed, timing.nonzero_exits) == (1, 1, 0)
            return str(raised.value)

    message = asyncio.run(exercise())
    assert "stdout tail" in message
    assert "stderr tail" in message
    assert ("earlier output omitted" in message) == large
    assert len(message) < 34000
    assert events[-1] == validation.ValidationCommandFinished(
        "nix build '.#demo'", succeeded=False
    )


@pytest.mark.parametrize("phase", ["packages", "roots"])
def test_incomplete_inventory_validation_without_update_plan(
    monkeypatch, tmp_path, capsys, phase
) -> None:
    """Full inventory validation must report failure even without a run monitor."""
    root = tmp_path / "repo"
    init_update_workspace_repo(root, tracked_files={".root": ""})
    monkeypatch.setattr(cli, "get_repo_root", lambda: root)
    monkeypatch.setattr(cli, "_build_run_plan", lambda *_args: None)
    monkeypatch.setattr(cli, "_get_updaters", dict)

    def incomplete(*_args, **_kwargs):
        raise validation.ValidationIncompleteError(
            "Validation incomplete: inventory deadline"
        )

    monkeypatch.setattr(
        validation,
        "validate_derivations",
        incomplete if phase == "packages" else lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(validation, "validate_root_closures", incomplete)
    assert (
        asyncio.run(
            cli.run_updates(UpdateOptions(json=True, validate_all_packages=True))
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is False
    assert result["errors"] == ["validation"]
    assert result["validationIncomplete"] == (
        "Validation incomplete: inventory deadline"
    )
    assert result["updated"] == []


@pytest.mark.parametrize("capture", ["streams", "exception", "quiet"])
@pytest.mark.parametrize("failure", ["timeout", "launch", "signal"])
@pytest.mark.parametrize("padding", [0, 20000])
def test_incomplete_diagnostics_redact_before_publication(
    tmp_path, capture, failure, padding
) -> None:
    """A severed URL and the original exception must not leak through tracebacks."""
    credential = "synthetic-private-value"
    url = (
        f"https://user:{credential}@example.test/file?token={'x' * padding}{credential}"
    )
    output = f"fetching {url}\nuseful final diagnostic\n"

    def run(args, **kwargs):
        if failure == "launch":
            message = f"cannot launch {url}"
            raise OSError(message)
        if failure == "signal":
            return subprocess.CompletedProcess(args, -9, output, output)
        if capture == "streams":
            kwargs["stdout"].write(output.encode())
            kwargs["stderr"].write(output.encode())
            raise subprocess.TimeoutExpired(args, 1)
        raise subprocess.TimeoutExpired(args, 1, output=output.encode(), stderr=output)

    with pytest.raises(validation.ValidationIncompleteError) as raised:
        validation._run_validation_command(
            ["nix", "build", url],
            cwd=tmp_path,
            timeout=1,
            run=run,
            sleep=lambda _: pytest.fail("incomplete execution must not retry"),
            progress=None if capture == "quiet" else lambda _: None,
        )
    assert credential not in "".join(traceback.format_exception(raised.value))
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    if failure != "launch":
        assert "useful final diagnostic" in str(raised.value)
    assert len(str(raised.value)) < 34000


@pytest.mark.parametrize("failure", ["timeout", "launch", "cancel"])
def test_parallel_incomplete_validation_reaps_siblings_before_return(
    monkeypatch, tmp_path, failure
) -> None:
    """A later failing group interrupts an earlier owned child and skips queued work."""
    ready = threading.Event()
    events = []
    started = []
    pid_file = tmp_path / "child.pid"
    original = validation._run_owned_validation_process

    def owned(args, **kwargs):
        target = args[-1].split("#", 1)[1]
        started.append(target)
        if target == "slow":
            return original(
                [
                    sys.executable,
                    "-c",
                    "import os,time,pathlib; "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                    "print('ready',flush=True); time.sleep(30)",
                ],
                **kwargs,
            )
        assert target == "fail"
        assert ready.wait(5), "sibling never started"
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "launch":
            raise OSError("primary launch failure")
        raise subprocess.TimeoutExpired(args, 1)

    def progress(event):
        events.append(event)
        if isinstance(event, validation.ValidationCommandOutput):
            ready.set()

    monkeypatch.setattr(validation, "_run_owned_validation_process", owned)
    expected = (
        asyncio.CancelledError
        if failure == "cancel"
        else validation.ValidationIncompleteError
    )
    before = time.monotonic()
    with pytest.raises(expected) as raised:
        validation.validate_derivation_requests(
            [
                validation.DerivationValidationRequest(name, f".#{name}")
                for name in ("slow", "fail", "queued")
            ],
            flake_root=tmp_path,
            max_eval_workers=2,
            progress=progress,
        )
    assert time.monotonic() - before < 5
    if failure == "launch":
        assert "primary launch failure" in str(raised.value)
    elif failure == "timeout":
        assert "timed out" in str(raised.value)
    assert set(started) == {"slow", "fail"}
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert sum(isinstance(e, validation.ValidationCommandStarted) for e in events) == 2
    assert sum(isinstance(e, validation.ValidationCommandFinished) for e in events) == 2
    _assert_reader_stopped()
