"""Tests for hash-file recovery planning and application."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from lib.recover import hashes as rh
from lib.recover.snapshot import SnapshotPlan
from lib.tests._assertions import check


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

    check(plan.deriver == "/nix/store/demo.drv")
    check(plan.snapshot == str(snapshot))
    check(plan.write_paths == ("flake.lock", "packages/demo/sources.json"))
    check(plan.remove_paths == ())


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

    check(plan.write_paths == ())
    check(plan.remove_paths == ("overlays/extra/sources.json",))


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

    check(
        changed
        == (
            "flake.lock",
            "packages/demo/sources.json",
            "overlays/extra/sources.json",
        )
    )
    check('"new": true' in (repo_root / "flake.lock").read_text(encoding="utf-8"))
    check((repo_root / "packages/demo/sources.json").exists())
    check(not (repo_root / "overlays/extra/sources.json").exists())
    args = seen["args"]
    check(isinstance(args, list))
    checked_args = args
    check(checked_args[0].endswith("git"))
    check(
        checked_args[1:]
        == [
            "add",
            "-A",
            "--",
            "flake.lock",
            "packages/demo/sources.json",
            "overlays/extra/sources.json",
        ]
    )
    check(seen["cwd"] == repo_root)


def test_run_hash_recovery_rejects_stage_without_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require --apply before staging changes."""
    rc = rh.run_hash_recovery(stage=True)
    check(rc == 1)
    check("--stage requires --apply" in capsys.readouterr().err)
