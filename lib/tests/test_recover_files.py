"""Tests for generic file recovery from source snapshots."""

from __future__ import annotations

import asyncio
import json
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


def test_plan_file_recovery_normalises_selectors_and_skips_non_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Deduplicate safe selectors and ignore directory-only matches."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "docs").mkdir()
    (repo_root / "docs/guide.md").write_text("same\n", encoding="utf-8")
    (repo_root / "docs/subdir").mkdir()

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "docs").mkdir()
    (snapshot / "docs/guide.md").write_text("same\n", encoding="utf-8")
    (snapshot / "docs/subdir").mkdir()

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
            paths=("docs/guide.md", "docs/guide.md "),
            globs=("docs/*", "docs/*"),
            repo_root=repo_root,
        )
    )

    assert plan.path_selectors == ("docs/guide.md",)
    assert plan.glob_selectors == ("docs/*",)
    assert plan.write_paths == ()
    assert plan.remove_paths == ()

    with pytest.raises(RuntimeError, match="must not be empty"):
        rf._normalise_selector("   ", kind="path")
    with pytest.raises(RuntimeError, match="must not escape the repo root"):
        rf._normalise_selector("../flake.lock", kind="path")
    with pytest.raises(RuntimeError, match="must point below the repo root"):
        rf._normalise_selector(".", kind="glob")


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


def test_apply_file_recovery_skips_missing_removals_without_staging(
    tmp_path: Path,
) -> None:
    """Do not report absent remove targets as changed paths."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "docs").mkdir()
    (snapshot / "docs/new.md").write_text("new\n", encoding="utf-8")

    plan = rf.FileRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot=str(snapshot),
        repo_root=str(repo_root),
        path_selectors=("docs/new.md",),
        glob_selectors=(),
        write_paths=("docs/new.md",),
        remove_paths=("docs/missing.md",),
    )

    assert rf.apply_file_recovery(plan) == ("docs/new.md",)
    assert (repo_root / "docs/new.md").read_text(encoding="utf-8") == "new\n"


def test_render_plain_covers_empty_selectors_and_apply_remove_branches() -> None:
    """Render the remaining plain-text summary branches for file recovery."""
    empty_plan = rf.FileRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        path_selectors=(),
        glob_selectors=(),
        write_paths=(),
        remove_paths=(),
    )

    plain = rf._render_plain(empty_plan, apply=False, stage=False, sync=False)
    assert "Path selectors" not in plain
    assert "Glob selectors" not in plain
    assert "Will restore: none" in plain

    remove_plan = rf.FileRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        path_selectors=(),
        glob_selectors=(),
        write_paths=(),
        remove_paths=("docs/remove.md",),
    )

    applied = rf._render_plain(
        remove_plan,
        apply=True,
        stage=False,
        sync=True,
        changed_paths=("docs/remove.md",),
    )
    assert "Removed (1):" in applied
    assert "  docs/remove.md" in applied
    assert "Applied changes: 1" in applied
    assert "Staged changes: yes" not in applied


def test_run_file_recovery_supports_plain_json_and_error_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render planning, apply, and failure paths for file recovery."""
    plan = rf.FileRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        path_selectors=("flake.lock",),
        glob_selectors=("docs/*.md",),
        write_paths=("flake.lock",),
        remove_paths=(),
    )

    seen_calls: list[tuple[tuple[str, ...], tuple[str, ...], bool]] = []

    async def _plan(
        _generation: str,
        *,
        globs: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
        sync: bool = False,
    ) -> rf.FileRecoveryPlan:
        seen_calls.append((globs, paths, sync))
        return plan

    monkeypatch.setattr(rf, "plan_file_recovery", _plan)
    monkeypatch.setattr(
        rf, "apply_file_recovery", lambda _plan, *, stage=False: ("flake.lock",)
    )

    assert (
        rf.run_file_recovery(paths=("flake.lock",), globs=("docs/*.md",), sync=True)
        == 0
    )
    plain = capsys.readouterr().out
    assert "Path selectors (1):" in plain
    assert "Glob selectors (1):" in plain
    assert "Will restore (1):" in plain
    assert "Will remove: none" in plain
    assert seen_calls == [(("docs/*.md",), ("flake.lock",), True)]

    assert (
        rf.run_file_recovery(
            apply=True,
            stage=True,
            json_output=True,
            paths=("flake.lock",),
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["apply"] is True
    assert payload["stage"] is True
    assert payload["sync"] is False
    assert payload["changed_paths"] == ["flake.lock"]
    assert payload["plan"]["path_selectors"] == ["flake.lock"]
    assert payload["plan"]["glob_selectors"] == ["docs/*.md"]
    assert payload["plan"]["write_paths"] == ["flake.lock"]
    assert payload["plan"]["remove_paths"] == []
    assert seen_calls == [
        (("docs/*.md",), ("flake.lock",), True),
        ((), ("flake.lock",), False),
    ]

    async def _boom(
        _generation: str,
        *,
        globs: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
        sync: bool = False,
    ) -> rf.FileRecoveryPlan:
        del globs, paths, sync
        raise RuntimeError("file planning failed")

    monkeypatch.setattr(rf, "plan_file_recovery", _boom)

    assert rf.run_file_recovery(paths=("flake.lock",), json_output=True) == 1
    assert json.loads(capsys.readouterr().out) == {
        "success": False,
        "error": "file planning failed",
    }
