"""Crash recovery at the real durable execution and repository boundaries."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import Coverage, CoverageData

from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.run_store import RunStore


def run_worker(
    root,
    run_root,
    operations,
    *,
    crash="",
    resume=None,
    json_output=False,
    scenario="",
    tty="off",
    run_id=None,
    patch_path=None,
) -> subprocess.CompletedProcess[str]:
    """Run the real process boundary and include its executed branches in coverage."""
    arguments = ["-m", "lib.tests._durable_worker", str(root), str(run_root)]
    if resume is not None:
        arguments.append(resume)
    coverage = Coverage.current()
    data_file = run_root.parent / "worker.coverage"
    if coverage is not None:
        config = run_root.parent / "worker.coveragerc"
        # Save coverage even at os._exit, without changing DBOS or filesystem state.
        config.write_text("[run]\nbranch = true\npatch = _exit\n")
        arguments = [
            "-m",
            "coverage",
            "run",
            "--rcfile",
            str(config),
            "--data-file",
            str(data_file),
            *arguments,
        ]
    result = subprocess.run(  # noqa: S603 -- fixed local fixture process
        [sys.executable, *arguments],
        env=os.environ
        | {
            "TEST_OPERATIONS": str(operations),
            "TEST_CRASH": crash,
            "TEST_JSON": "1" if json_output else "0",
            "TEST_SCENARIO": scenario,
            "TEST_TTY": tty,
            "TEST_RUN_ID": run_id or "",
            "TEST_PATCH": str(patch_path) if patch_path else "",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if coverage is not None:
        data = CoverageData(basename=str(data_file))
        data.read()
        coverage.get_data().update(data)
    return result


def test_ci_rerun_recovers_and_exports_the_recorded_candidate(tmp_path: Path) -> None:
    """A stable job ID resumes, then re-exports only validated bytes after completion."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            **{
                f"packages/{name}/updater.py": "# fixture" for name in ("alpha", "beta")
            },
            "notes": "original",
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    # Also covers death between creating SQLite and saving the initial request.
    RunStore(run_root / "update")
    patch_path = tmp_path / "update.patch"
    first = run_worker(
        root, run_root, operations, run_id="update", crash="acknowledgement"
    )
    assert first.returncode == 42, first.stdout + first.stderr
    resumed = run_worker(
        root, run_root, operations, run_id="update", patch_path=patch_path
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    original_patch = patch_path.read_bytes()
    original_operations = operations.read_text()
    (root / "notes").write_text("later unvalidated edit")
    (root / "packages/alpha/generated.txt").write_text("unvalidated")
    patch_path.unlink()
    repeated = run_worker(
        root, run_root, operations, run_id="update", patch_path=patch_path
    )
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert patch_path.read_bytes() == original_patch
    assert operations.read_text() == original_operations
    assert {p.name for p in run_root.iterdir() if not p.is_symlink()} == {"update"}
    clone = tmp_path / "consumer"
    subprocess.run(  # noqa: S603 -- local test repositories
        ["git", "clone", str(root), str(clone)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "apply", str(patch_path)], cwd=clone, check=True)  # noqa: S603, S607
    assert (clone / "packages/alpha/generated.txt").read_text() == "2.0"
    assert (clone / "notes").read_text() == "original"


@pytest.mark.parametrize(
    "crash",
    ["source", "source-result", "cancel", "validation", "promotion", "acknowledgement"],
)
def test_resume_reuses_work_and_promotes_exact_candidate(
    tmp_path: Path, crash: str
) -> None:
    """A process can die at each effect boundary without losing completed work."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/alpha/updater.py": "# fixture\n",
            "packages/beta/updater.py": "# fixture\n",
            "notes": "committed",
        },
    )
    (root / "notes").write_text("uncommitted")
    run_root = tmp_path / "runs"
    operations = tmp_path / "operations"
    first = run_worker(root, run_root, operations, crash=crash)
    assert first.returncode == (-2 if crash == "cancel" else 42), (
        first.stdout + first.stderr
    )
    if crash == "cancel":
        assert "cleanup" in operations.read_text().splitlines()
    (run_dir,) = (
        path for path in run_root.iterdir() if path.is_dir() and not path.is_symlink()
    )
    store = RunStore(run_dir, readonly=True)
    assert store.read("baseline")
    assert store.execution_status() == "PENDING"
    resumed = run_worker(
        root, run_root, operations, resume=run_dir.name, json_output=True
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    report = json.loads(resumed.stdout)
    assert report["updated"] == ["alpha", "beta"]
    assert report["timings"]
    assert store.execution_status() == "SUCCESS"
    for name in ("alpha", "beta"):
        assert (root / f"packages/{name}/generated.txt").read_text() == "2.0"
        assert operations.read_text().splitlines().count(f"resolve:{name}") == 1
    assert operations.read_text().splitlines().count("hash:alpha:2.0") == 1
    assert operations.read_text().splitlines().count("generate:alpha") == 1
    assert (root / "notes").read_text() == "uncommitted"
    assert not list((root / ".git").glob("*transaction*"))


@pytest.mark.parametrize("crash", ["promotion", "acknowledgement"])
def test_pending_resume_preserves_intervening_live_edits(
    tmp_path: Path, crash: str
) -> None:
    """Neither unfinished nor already-applied candidates authorize overwriting later edits."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/alpha/updater.py": "# fixture",
            "packages/beta/updater.py": "# fixture",
            "notes": "original",
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    assert run_worker(root, run_root, operations, crash=crash).returncode == 42
    (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
    (root / "notes").write_text("external edit")
    resumed = run_worker(
        root, run_root, operations, resume=run_dir.name, json_output=True
    )
    assert resumed.returncode == 1, resumed.stdout + resumed.stderr
    result = json.loads(resumed.stdout)
    assert not result["success"]
    assert "notes" in result["error"]
    assert (root / "notes").read_text() == "external edit"
    assert (root / "packages/alpha/generated.txt").exists() == (
        crash == "acknowledgement"
    )
    assert operations.read_text().splitlines().count("hash:alpha:2.0") == 1


def test_completed_run_reuses_recorded_result_in_one_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The public lifecycle can stop and reopen DBOS without reapplying a completed run."""
    import asyncio

    from lib.tests._durable_worker import run_fixture
    from lib.update.run_monitor import load_run_status

    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/alpha/updater.py": "# fixture",
            "packages/beta/updater.py": "# fixture",
            "notes": "untouched",
        },
    )
    operations = tmp_path / "operations"
    monkeypatch.setenv("TEST_OPERATIONS", str(operations))
    run_root = tmp_path / "runs"

    async def run(resume=None):
        with pytest.MonkeyPatch.context() as patch:
            return await run_fixture(root, run_root, patch, resume=resume)

    # DBOS owns a process-global stderr logger. Keep its stream alive across
    # repeated service starts rather than binding it to pytest's capture stream.
    with capsys.disabled():
        assert asyncio.run(run()) == 0
        (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
        initial = operations.read_text()
        (root / "notes").write_text("later edit")
        assert asyncio.run(run(run_dir.name)) == 0
    assert operations.read_text() == initial
    assert (root / "notes").read_text() == "later edit"
    _, status, _ = load_run_status(run_root, run_dir.name)
    assert status.execution_status == "SUCCESS"
    assert status.finished


def test_failed_source_replays_original_error_without_reexecuting(
    tmp_path: Path,
) -> None:
    """A recorded domain failure survives root recovery and independent promotion."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            f"packages/{name}/updater.py": "# fixture"
            for name in ("alpha", "beta", "gamma")
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    first = run_worker(
        root, run_root, operations, crash="validation", scenario="failure"
    )
    assert first.returncode == 42, first.stdout + first.stderr
    (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
    resumed = run_worker(
        root,
        run_root,
        operations,
        resume=run_dir.name,
        json_output=True,
        scenario="failure",
        patch_path=tmp_path / "failed.patch",
    )
    assert resumed.returncode == 1, resumed.stdout + resumed.stderr
    result = json.loads(resumed.stdout)
    assert result["updated"] == ["alpha", "beta"]
    assert result["errors"] == ["gamma"]
    assert (tmp_path / "failed.patch").read_bytes() == b""
    assert operations.read_text().splitlines().count("fail:gamma") == 1
    events = RunStore(run_dir, readonly=True).events()
    assert "original source failure" in json.dumps(events)


def test_dynamic_updater_retry_error_roundtrips() -> None:
    """DBOS can reconstruct a typed retry signal from a dynamically loaded updater."""
    import pickle
    import sys

    from lib.update.updaters import UPDATERS, ensure_updaters_loaded

    ensure_updaters_loaded()
    module = sys.modules[UPDATERS["zen-twilight"].__module__]
    error = module._TwilightSnapshotChangedError(
        phase="after", expected="1", observed="2"
    )
    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 -- trusted local value
    assert type(restored) is type(error)
    assert (restored.phase, restored.expected, restored.observed) == ("after", "1", "2")
    assert str(restored) == str(error)


def test_resume_replays_checkpointed_nix_failure(tmp_path: Path) -> None:
    """Crash before the source result commits: its failed step must still replay."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            f"packages/{name}/updater.py": "# fixture"
            for name in ("alpha", "beta", "gamma")
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    first = run_worker(
        root, run_root, operations, crash="source-error", scenario="nix-failure"
    )
    assert first.returncode == 42, first.stdout + first.stderr
    (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
    store = RunStore(run_dir, readonly=True)
    previous_events = len(store.events())
    resumed = run_worker(
        root,
        run_root,
        operations,
        resume=run_dir.name,
        json_output=True,
        scenario="nix-failure",
    )
    assert resumed.returncode == 1, resumed.stdout + resumed.stderr
    report = json.loads(resumed.stdout)
    assert report["updated"] == ["alpha", "beta"]
    assert report["errors"] == ["gamma"]
    errors = [
        event
        for event in store.events()[previous_events:]
        if event.get("kind") == "error" and event.get("source") == "gamma"
    ]
    assert errors
    assert all("NixCommandError" in event["message"] for event in errors)
    assert all("fixture transfer failed" in event["message"] for event in errors)
    assert operations.read_text().splitlines().count("fail:gamma") == 1


@pytest.mark.parametrize(("original", "resumed"), [("force", "off"), ("off", "force")])
def test_resume_uses_current_presentation_options(
    tmp_path: Path, original: str, resumed: str
) -> None:
    """Source and phase rendering follow this invocation, not the durable plan."""
    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            f"packages/{name}/updater.py": "# fixture" for name in ("alpha", "beta")
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    first = run_worker(root, run_root, operations, crash="validation", tty=original)
    assert first.returncode == 42, first.stdout + first.stderr
    (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
    second = run_worker(root, run_root, operations, resume=run_dir.name, tty=resumed)
    assert second.returncode == 0, second.stdout + second.stderr
    assert [
        line
        for line in operations.read_text().splitlines()
        if line.startswith("live-ui:")
    ] == [f"live-ui:{original == 'force'}", f"live-ui:{resumed == 'force'}"]
    assert ("Phase 2: sources.json updates" in second.stdout) == (resumed == "off")


def test_replayed_phase_outcomes_rebuild_progress_and_preserve_failures(
    tmp_path: Path,
) -> None:
    """Refs and failed input refreshes must be as observable after replay as source tasks."""
    from lib.update.run_monitor import load_run_status

    root = tmp_path / "repo"
    init_update_workspace_repo(
        root,
        tracked_files={
            f"packages/{name}/updater.py": "# fixture"
            for name in ("alpha", "beta", "gamma")
        },
    )
    run_root, operations = tmp_path / "runs", tmp_path / "operations"
    first = run_worker(
        root, run_root, operations, crash="validation", scenario="phases"
    )
    assert first.returncode == 42, first.stdout + first.stderr
    (run_dir,) = (p for p in run_root.iterdir() if not p.is_symlink())
    resumed = run_worker(
        root,
        run_root,
        operations,
        resume=run_dir.name,
        json_output=True,
        scenario="phases",
    )
    assert resumed.returncode == 1, resumed.stdout + resumed.stderr
    report = json.loads(resumed.stdout)
    assert report["updated"] == ["alpha", "beta"]
    assert set(report["errors"]) == {"bad-ref", "gamma"}
    for operation in ("ref:good-ref", "ref:bad-ref", "input-refresh"):
        assert operations.read_text().splitlines().count(operation) == 1
    assert "fail:gamma" not in operations.read_text()
    _, status, _ = load_run_status(run_root, run_dir.name)
    assert (status.pending, status.failed, status.no_change) == (0, 2, 1)
