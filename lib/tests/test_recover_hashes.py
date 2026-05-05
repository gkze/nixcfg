"""Tests for hash-file recovery planning and application."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from lib.recover import hashes as rh
from lib.recover.snapshot import SnapshotPlan

if TYPE_CHECKING:
    import pytest


def _write_markers(root: Path) -> None:
    (root / "flake.nix").write_text("{}\n", encoding="utf-8")
    (root / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    (root / "nixcfg.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    modules_dir = root / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "common.nix").write_text("{}\n", encoding="utf-8")


def _write_source(root: Path, relative_path: str, payload: object) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_plan_hash_recovery_detects_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan changed files from the matched source snapshot."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "flake.lock").write_text('{"nodes": {"x": 1}}\n', encoding="utf-8")
    _write_source(repo_root, "packages/demo/sources.json", {"version": "old"})

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _write_markers(snapshot)
    (snapshot / "flake.lock").write_text('{"nodes": {"x": 2}}\n', encoding="utf-8")
    _write_source(snapshot, "packages/demo/sources.json", {"version": "new"})

    realised = tmp_path / "realised"
    realised.write_text("out\n", encoding="utf-8")
    generation = tmp_path / "current-system"
    generation.symlink_to(realised)

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation=str(generation),
            resolved_target=str(realised),
            deriver="/nix/store/demo.drv",
            snapshot=str(snapshot),
        )

    monkeypatch.setattr(rh, "plan_snapshot_recovery", _plan_snapshot)

    plan = asyncio.run(rh.plan_hash_recovery(str(generation), repo_root=repo_root))

    assert plan.deriver == "/nix/store/demo.drv"
    assert plan.snapshot == str(snapshot)
    assert plan.write_paths == ("flake.lock", "packages/demo/sources.json")
    assert plan.remove_paths == ()


def test_plan_hash_recovery_sync_marks_local_only_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sync mode schedules managed files absent from the snapshot for removal."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    _write_source(repo_root, "packages/demo/sources.json", {"version": "same"})
    _write_source(repo_root, "overlays/extra/sources.json", {"version": "remove"})

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _write_markers(snapshot)
    (snapshot / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    _write_source(snapshot, "packages/demo/sources.json", {"version": "same"})

    realised = tmp_path / "realised"
    realised.write_text("out\n", encoding="utf-8")

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation=str(realised),
            resolved_target=str(realised),
            deriver="/nix/store/demo.drv",
            snapshot=str(snapshot),
        )

    monkeypatch.setattr(rh, "plan_snapshot_recovery", _plan_snapshot)

    plan = asyncio.run(
        rh.plan_hash_recovery(str(realised), repo_root=repo_root, sync=True)
    )

    assert plan.write_paths == ()
    assert plan.remove_paths == ("overlays/extra/sources.json",)


def test_apply_hash_recovery_writes_deletes_and_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Apply the plan to disk and stage the affected paths."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "flake.lock").write_text(
        '{"nodes": {"old": true}}\n', encoding="utf-8"
    )
    _write_source(repo_root, "overlays/extra/sources.json", {"remove": True})

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _write_markers(snapshot)
    (snapshot / "flake.lock").write_text('{"nodes": {"new": true}}\n', encoding="utf-8")
    _write_source(snapshot, "packages/demo/sources.json", {"version": "new"})

    plan = rh.HashRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot=str(snapshot),
        repo_root=str(repo_root),
        write_paths=("flake.lock", "packages/demo/sources.json"),
        remove_paths=("overlays/extra/sources.json",),
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
        seen["check"] = check
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _run)

    changed = rh.apply_hash_recovery(plan, stage=True)

    assert changed == (
        "flake.lock",
        "packages/demo/sources.json",
        "overlays/extra/sources.json",
    )
    assert (
        json.loads((repo_root / "flake.lock").read_text(encoding="utf-8"))["nodes"][
            "new"
        ]
        is True
    )
    assert (repo_root / "packages/demo/sources.json").exists()
    assert not (repo_root / "overlays/extra/sources.json").exists()
    args = seen["args"]
    assert isinstance(args, list)
    checked_args = args
    assert checked_args[0].endswith("git")
    assert checked_args[1:] == [
        "add",
        "-A",
        "--",
        "flake.lock",
        "packages/demo/sources.json",
        "overlays/extra/sources.json",
    ]
    assert seen["cwd"] == repo_root


def test_run_hash_recovery_rejects_stage_without_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require --apply before staging changes."""
    rc = rh.run_hash_recovery(stage=True)
    assert rc == 1
    assert "--stage requires --apply" in capsys.readouterr().err


def test_plan_hash_recovery_to_dict_and_managed_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Normalise managed paths and include plan dict rendering."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    _write_source(repo_root, "packages/demo/sources.json", {"version": "same"})

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _write_markers(snapshot)
    (snapshot / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    _write_source(snapshot, "packages/demo/sources.json", {"version": "same"})

    async def _plan_snapshot(_generation: str) -> SnapshotPlan:
        return SnapshotPlan(
            generation="/run/current-system",
            resolved_target="/nix/store/current-system",
            deriver="/nix/store/demo.drv",
            snapshot=str(snapshot),
        )

    monkeypatch.setattr(rh, "plan_snapshot_recovery", _plan_snapshot)

    assert rh._managed_relative_paths(repo_root) == {
        "flake.lock",
        "packages/demo/sources.json",
    }
    plan = asyncio.run(rh.plan_hash_recovery(repo_root=repo_root))
    assert plan.to_dict()["write_paths"] == ()


def test_apply_hash_recovery_skips_missing_removals_without_staging(
    tmp_path: Path,
) -> None:
    """Do not stage or fail when a scheduled removal is already absent."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "flake.lock").write_text('{"nodes": {"new": true}}\n', encoding="utf-8")

    plan = rh.HashRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot=str(snapshot),
        repo_root=str(repo_root),
        write_paths=("flake.lock",),
        remove_paths=("overlays/missing/sources.json",),
    )

    assert rh.apply_hash_recovery(plan) == ("flake.lock",)
    assert (
        json.loads((repo_root / "flake.lock").read_text(encoding="utf-8"))["nodes"][
            "new"
        ]
        is True
    )


def test_render_plain_covers_empty_restore_and_apply_remove_branches() -> None:
    """Render the remaining plain-text summary branches for hash recovery."""
    empty_plan = rh.HashRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        write_paths=(),
        remove_paths=(),
    )

    plain = rh._render_plain(empty_plan, apply=False, stage=False, sync=False)
    assert "Will restore: none" in plain

    remove_plan = rh.HashRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        write_paths=(),
        remove_paths=("packages/demo/sources.json",),
    )

    applied = rh._render_plain(
        remove_plan,
        apply=True,
        stage=False,
        sync=True,
        changed_paths=("packages/demo/sources.json",),
    )
    assert "Removed (1):" in applied
    assert "  packages/demo/sources.json" in applied
    assert "Applied changes: 1" in applied
    assert "Staged changes: yes" not in applied


def test_run_hash_recovery_supports_plain_json_and_error_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render planning, applied, and failure results through the CLI boundary."""
    plan = rh.HashRecoveryPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
        repo_root="/repo",
        write_paths=("flake.lock",),
        remove_paths=(),
    )

    seen_sync: list[bool] = []

    async def _plan(_generation: str, *, sync: bool = False) -> rh.HashRecoveryPlan:
        seen_sync.append(sync)
        return plan

    monkeypatch.setattr(rh, "plan_hash_recovery", _plan)
    monkeypatch.setattr(
        rh, "apply_hash_recovery", lambda _plan, *, stage=False: ("flake.lock",)
    )

    assert rh.run_hash_recovery(sync=True) == 0
    plain = capsys.readouterr().out
    assert "Will restore (1):" in plain
    assert "Will remove: none" in plain
    assert seen_sync == [True]

    assert rh.run_hash_recovery(apply=True, stage=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["apply"] is True
    assert payload["stage"] is True
    assert payload["sync"] is False
    assert payload["changed_paths"] == ["flake.lock"]
    assert payload["plan"]["write_paths"] == ["flake.lock"]
    assert payload["plan"]["remove_paths"] == []
    assert payload["plan"]["snapshot"] == plan.snapshot
    assert seen_sync == [True, False]

    async def _boom(_generation: str, *, sync: bool = False) -> rh.HashRecoveryPlan:
        del sync
        raise RuntimeError("hash planning failed")

    monkeypatch.setattr(rh, "plan_hash_recovery", _boom)

    assert rh.run_hash_recovery(json_output=True) == 1
    assert json.loads(capsys.readouterr().out) == {
        "success": False,
        "error": "hash planning failed",
    }
