"""Tests for shared recover command helpers."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from lib.recover import _common as rc


def test_files_equal_requires_existing_identical_files(tmp_path: Path) -> None:
    """Compare files by bytes and reject missing inputs."""
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"

    assert rc.files_equal(left, right) is False

    left.write_bytes(b"same")
    right.write_bytes(b"same")
    assert rc.files_equal(left, right) is True

    right.write_bytes(b"different")
    assert rc.files_equal(left, right) is False


def test_files_equal_compares_symlink_targets_and_exec_bit(tmp_path: Path) -> None:
    """Git tracks file type, symlink target and exec bit, not just bytes."""
    data = tmp_path / "data.txt"
    data.write_bytes(b"same")
    copy = tmp_path / "copy.txt"
    copy.write_bytes(b"same")
    link = tmp_path / "link"
    link.symlink_to("data.txt")
    same_link = tmp_path / "same-link"
    same_link.symlink_to("data.txt")
    other_link = tmp_path / "other-link"
    other_link.symlink_to("copy.txt")

    assert rc.files_equal(link, same_link) is True
    assert rc.files_equal(link, other_link) is False
    assert rc.files_equal(link, copy) is False
    assert rc.files_equal(copy, link) is False

    copy.chmod(0o755)
    assert rc.files_equal(data, copy) is False


def test_apply_recovery_restores_symlinks_and_exec_bits(tmp_path: Path) -> None:
    """Restored entries match the snapshot in type, target, bytes and mode."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    (snapshot / "bin").mkdir(parents=True)
    (repo / "bin").mkdir(parents=True)
    (snapshot / "bin" / "tool").write_bytes(b"#!/bin/sh\n")
    (snapshot / "bin" / "tool").chmod(0o555)
    (snapshot / "data.txt").write_bytes(b"data")
    (snapshot / "data.txt").chmod(0o444)
    (snapshot / "new" / "nested").mkdir(parents=True)
    (snapshot / "new" / "nested" / "link").symlink_to("../../data.txt")
    (snapshot / "was-link").write_bytes(b"plain")
    (repo / "data.txt").write_bytes(b"stale")
    (repo / "data.txt").chmod(0o755)
    (repo / "was-link").symlink_to("data.txt")
    paths = ("bin/tool", "data.txt", "new/nested/link", "was-link")
    write_paths, _ = rc.compute_recovery_paths(
        snapshot_root=snapshot,
        repo_root=repo,
        snapshot_paths=set(paths),
        current_paths=set(),
        sync=False,
    )
    assert write_paths == paths
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=write_paths,
        remove_paths=(),
    )

    assert rc.apply_recovery(plan) == paths

    assert (repo / "bin" / "tool").stat().st_mode & 0o777 == 0o755
    assert (repo / "data.txt").stat().st_mode & 0o777 == 0o644
    assert (repo / "new" / "nested" / "link").readlink() == Path("../../data.txt")
    assert not (repo / "was-link").is_symlink()
    assert (repo / "was-link").stat().st_mode & 0o777 == 0o644
    assert all(rc.files_equal(snapshot / path, repo / path) for path in paths)
    assert sorted(path.name for path in repo.rglob(".*.tmp")) == []


def test_restore_symlinks_never_reuses_fixed_temp_names(tmp_path: Path) -> None:
    """Unique owned temps cannot delete unrelated or separately selected paths."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    snapshot.mkdir()
    repo.mkdir()
    selected = (".link.recover.tmp", "link", "other")
    for name in selected:
        (snapshot / name).symlink_to(f"{name}.target")
    (repo / ".other.recover.tmp").write_text("unrelated\n", encoding="utf-8")
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=selected,
        remove_paths=(),
    )

    assert rc.apply_recovery(plan) == selected

    for name in selected:
        assert (repo / name).readlink() == Path(f"{name}.target")
    assert (repo / ".other.recover.tmp").read_text(encoding="utf-8") == "unrelated\n"
    assert list(repo.glob(".*.recover.*.tmp")) == []


def test_run_recovery_chmod_failure_preserves_target_and_reports_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mode selection finishes on the temp before the live target can change."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    snapshot.mkdir()
    repo.mkdir()
    for name in ("first", "second"):
        (snapshot / name).write_text(f"recovered {name}\n", encoding="utf-8")
        (repo / name).write_text(f"stale {name}\n", encoding="utf-8")
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=("first", "second"),
        remove_paths=(),
    )
    original_chmod = Path.chmod

    def fail_second_temp_chmod(
        path: Path, mode: int, *, follow_symlinks: bool = True
    ) -> None:
        if path.name == "entry" and path.parent.name.startswith(".second.recover."):
            raise OSError("chmod failed")
        original_chmod(path, mode, follow_symlinks=follow_symlinks)

    async def plan_fn() -> rc.RecoveryPlan:
        return plan

    monkeypatch.setattr(Path, "chmod", fail_second_temp_chmod)
    status = rc.run_recovery(
        plan_fn=plan_fn,
        apply_fn=rc.apply_recovery,
        apply=True,
        json_output=True,
        stage=True,
        sync=False,
    )

    assert status == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["completed_paths"] == ["first"]
    assert payload["in_flight_paths"] == ["second"]
    assert payload["stage_state"] == "pending"
    assert (repo / "first").read_text(encoding="utf-8") == "recovered first\n"
    assert (repo / "second").read_text(encoding="utf-8") == "stale second\n"
    assert list(repo.glob(".*.recover.*.tmp")) == []


@pytest.mark.parametrize("operation", ["second-write", "removal"])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_run_recovery_reports_in_flight_mutation_before_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    exception_type: type[BaseException],
) -> None:
    """Cancellation retains partial JSON state and then propagates unchanged."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    snapshot.mkdir()
    repo.mkdir()
    for name in ("first", "second"):
        (snapshot / name).write_text(f"recovered {name}\n", encoding="utf-8")
        (repo / name).write_text(f"stale {name}\n", encoding="utf-8")
    (repo / "extra").write_text("remove\n", encoding="utf-8")
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=("first", "second") if operation == "second-write" else ("first",),
        remove_paths=() if operation == "second-write" else ("extra",),
    )
    error = exception_type()

    if operation == "second-write":
        restore = rc._restore_path

        def interrupt_second_write(source: Path, target: Path) -> None:
            if target.name == "second":
                raise error
            restore(source, target)

        monkeypatch.setattr(rc, "_restore_path", interrupt_second_write)
    else:
        unlink = Path.unlink

        def interrupt_after_removal(path: Path, *, missing_ok: bool = False) -> None:
            if path == repo / "extra":
                unlink(path, missing_ok=missing_ok)
                raise error
            unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", interrupt_after_removal)

    async def plan_fn() -> rc.RecoveryPlan:
        return plan

    with pytest.raises(exception_type) as exc_info:
        rc.run_recovery(
            plan_fn=plan_fn,
            apply_fn=rc.apply_recovery,
            apply=True,
            json_output=True,
            stage=True,
            sync=True,
        )

    assert exc_info.value is error
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["completed_write_paths"] == ["first"]
    assert payload["completed_remove_paths"] == []
    assert payload["in_flight_paths"] == [
        "second" if operation == "second-write" else "extra"
    ]
    assert payload["stage_state"] == "pending"
    if operation == "removal":
        assert not (repo / "extra").exists()


@pytest.mark.parametrize(
    ("stage", "stage_state"),
    [(False, "not_requested"), (True, "pending")],
)
def test_apply_recovery_reports_completed_writes_after_a_later_failure(
    tmp_path: Path, stage: bool, stage_state: str
) -> None:
    """A failed later write exposes earlier changes without implying rollback."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    snapshot.mkdir()
    repo.mkdir()
    (snapshot / "first").write_text("recovered\n", encoding="utf-8")
    (repo / "first").write_text("stale\n", encoding="utf-8")
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=("first", "missing"),
        remove_paths=(),
    )

    with pytest.raises(rc.RecoveryApplyError, match="applied 1 of 2") as exc_info:
        rc.apply_recovery(plan, stage=stage)

    error = exc_info.value
    assert isinstance(error.__cause__, FileNotFoundError)
    assert error.completed_paths == ("first",)
    assert error.completed_write_paths == ("first",)
    assert error.completed_remove_paths == ()
    assert error.in_flight_paths == ("missing",)
    assert error.planned_count == 2
    assert error.stage_requested is stage
    assert error.stage_attempted is False
    assert error.stage_state == stage_state
    assert (repo / "first").read_text(encoding="utf-8") == "recovered\n"


def test_run_recovery_reports_completed_changes_after_stage_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Staging failure retains structured write/remove state at the CLI boundary."""
    snapshot = tmp_path / "snapshot"
    repo = tmp_path / "repo"
    snapshot.mkdir()
    repo.mkdir()
    (snapshot / "flake.lock").write_text("recovered\n", encoding="utf-8")
    (repo / "flake.lock").write_text("stale\n", encoding="utf-8")
    (repo / "extra").write_text("remove\n", encoding="utf-8")
    plan = rc.RecoveryPlan(
        generation="g",
        resolved_target="t",
        deriver="d",
        snapshot=str(snapshot),
        repo_root=str(repo),
        write_paths=("flake.lock",),
        remove_paths=("extra",),
    )

    def fail_stage(_root: Path, _paths: tuple[str, ...]) -> None:
        raise RuntimeError("stage failed")

    async def plan_fn() -> rc.RecoveryPlan:
        return plan

    monkeypatch.setattr(rc, "stage_paths", fail_stage)
    status = rc.run_recovery(
        plan_fn=plan_fn,
        apply_fn=rc.apply_recovery,
        apply=True,
        json_output=True,
        stage=True,
        sync=True,
    )

    assert status == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert "stage failed" in payload["error"]
    assert "applied 2 of 2" in payload["error"]
    assert payload["completed_paths"] == ["flake.lock", "extra"]
    assert payload["completed_write_paths"] == ["flake.lock"]
    assert payload["completed_remove_paths"] == ["extra"]
    assert payload["in_flight_paths"] == []
    assert payload["stage_state"] == "failed"
    assert (repo / "flake.lock").read_text(encoding="utf-8") == "recovered\n"
    assert not (repo / "extra").exists()


def test_stage_paths_returns_early_when_no_paths(tmp_path: Path) -> None:
    """Skip git lookup entirely when there is nothing to stage."""
    rc.stage_paths(tmp_path, ())


def test_stage_paths_rejects_missing_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Raise a clear error when git cannot be found."""
    monkeypatch.setattr("lib.recover._common.shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError, match="git not found on PATH"):
        rc.stage_paths(tmp_path, ("flake.lock",))


def test_stage_paths_runs_git_add_and_surfaces_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run git add -A and propagate stderr or stdout failures."""
    seen: dict[str, object] = {}

    def _run_success(
        args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        seen["args"] = args
        seen["cwd"] = cwd
        seen["check"] = check
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(
        "lib.recover._common.shutil.which", lambda _name: "/usr/bin/git"
    )
    monkeypatch.setattr(subprocess, "run", _run_success)

    rc.stage_paths(tmp_path, ("flake.lock", "packages/demo/sources.json"))

    assert seen["args"] == [
        "/usr/bin/git",
        "add",
        "-A",
        "--",
        "flake.lock",
        "packages/demo/sources.json",
    ]
    assert seen["cwd"] == tmp_path
    assert seen["check"] is False
    assert seen["capture_output"] is True
    assert seen["text"] is True

    def _run_stderr(
        _args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        return subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="fatal: bad path\n"
        )

    monkeypatch.setattr(subprocess, "run", _run_stderr)
    with pytest.raises(RuntimeError, match="fatal: bad path"):
        rc.stage_paths(tmp_path, ("flake.lock",))

    def _run_stdout(
        _args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check, capture_output, text
        return subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="needs attention\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _run_stdout)
    with pytest.raises(RuntimeError, match="needs attention"):
        rc.stage_paths(tmp_path, ("flake.lock",))
