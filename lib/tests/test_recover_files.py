"""Tests for generic file recovery from source snapshots."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from lib.recover import files as rf
from lib.recover.snapshot import SnapshotPlan


def test_plan_file_recovery_detects_writes_from_paths_and_globs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Plan writes for selected files that differ from the snapshot."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "flake.lock").write_text("old\n", encoding="utf-8")
    (repo_root / "docs").mkdir()
    (repo_root / "docs/guide.md").write_text("old guide\n", encoding="utf-8")

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "flake.lock").write_text("new\n", encoding="utf-8")
    (snapshot / "docs").mkdir()
    (snapshot / "docs/guide.md").write_text("new guide\n", encoding="utf-8")

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation="/run/current-system",
            resolved_target="/nix/store/current-system",
            deriver="/nix/store/demo.drv",
            snapshot=str(snapshot),
        )

    monkeypatch.setattr(rf, "plan_snapshot_recovery", _plan_snapshot)

    plan = asyncio.run(
        rf.plan_file_recovery(
            "/run/current-system",
            globs=("docs/*.md",),
            paths=("flake.lock",),
            repo_root=repo_root,
        )
    )

    assert plan.path_selectors == ("flake.lock",)
    assert plan.glob_selectors == ("docs/*.md",)
    assert plan.write_paths == ("docs/guide.md", "flake.lock")
    assert plan.remove_paths == ()


def test_plan_file_recovery_sync_marks_selected_local_only_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sync mode removes selected files missing from the snapshot."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "docs").mkdir()
    (repo_root / "docs/extra.md").write_text("remove\n", encoding="utf-8")

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation="/run/current-system",
            resolved_target="/nix/store/current-system",
            deriver="/nix/store/demo.drv",
            snapshot=str(snapshot),
        )

    monkeypatch.setattr(rf, "plan_snapshot_recovery", _plan_snapshot)

    plan = asyncio.run(
        rf.plan_file_recovery(
            "/run/current-system",
            globs=("docs/*.md",),
            repo_root=repo_root,
            sync=True,
        )
    )

    assert plan.write_paths == ()
    assert plan.remove_paths == ("docs/extra.md",)


def test_plan_file_recovery_rejects_missing_or_invalid_selectors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require at least one safe repo-relative selector."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation="/run/current-system",
            resolved_target="/nix/store/current-system",
            deriver="/nix/store/demo.drv",
            snapshot=str(tmp_path / "snapshot"),
        )

    monkeypatch.setattr(rf, "plan_snapshot_recovery", _plan_snapshot)

    with pytest.raises(RuntimeError, match="At least one --path or --glob"):
        asyncio.run(rf.plan_file_recovery(repo_root=repo_root))

    with pytest.raises(RuntimeError, match="repo-relative"):
        asyncio.run(rf.plan_file_recovery(paths=("/tmp/abs",), repo_root=repo_root))

    with pytest.raises(RuntimeError, match="No files matched"):
        asyncio.run(
            rf.plan_file_recovery(
                paths=("missing.txt",),
                repo_root=repo_root,
            )
        )


def test_apply_file_recovery_writes_deletes_and_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Apply selected file recovery and stage the touched paths."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "docs").mkdir()
    (repo_root / "docs/old.md").write_text("old\n", encoding="utf-8")
    (repo_root / "docs/remove.md").write_text("remove\n", encoding="utf-8")

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "docs").mkdir()
    (snapshot / "docs/old.md").write_bytes(b"new\n")

    plan = rf.FileRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot=str(snapshot),
        repo_root=str(repo_root),
        path_selectors=("docs/old.md",),
        glob_selectors=("docs/*.md",),
        write_paths=("docs/old.md",),
        remove_paths=("docs/remove.md",),
    )

    seen: dict[str, object] = {}

    def _run(
        args: list[str],
        *,
        cwd: Path,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        seen["args"] = args
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _run)

    changed = rf.apply_file_recovery(plan, stage=True)

    assert changed == ("docs/old.md", "docs/remove.md")
    assert (repo_root / "docs/old.md").read_bytes() == b"new\n"
    assert not (repo_root / "docs/remove.md").exists()
    args = seen["args"]
    assert isinstance(args, list)
    checked_args = args
    assert checked_args[0].endswith("git")
    assert checked_args[1:] == ["add", "-A", "--", "docs/old.md", "docs/remove.md"]
    assert seen["cwd"] == repo_root


def test_run_file_recovery_rejects_stage_without_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require --apply before staging selected file changes."""
    rc = rf.run_file_recovery(stage=True, paths=("flake.lock",))
    assert rc == 1
    assert "--stage requires --apply" in capsys.readouterr().err
