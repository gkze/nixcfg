"""Tests for source snapshot recovery planning."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lib.recover import snapshot as rs
from lib.tests._assertions import check


def _write_markers(root: Path) -> None:
    (root / "flake.nix").write_text("{}\n", encoding="utf-8")
    (root / "flake.lock").write_text('{"nodes": {}}\n', encoding="utf-8")
    (root / "nixcfg.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    modules_dir = root / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "common.nix").write_text("{}\n", encoding="utf-8")


def test_plan_snapshot_recovery_resolves_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve the snapshot from a realised generation path."""
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    _write_markers(snapshot)

    realised = tmp_path / "realised"
    realised.write_text("out\n", encoding="utf-8")
    generation = tmp_path / "current-system"
    generation.symlink_to(realised)

    async def _deriver(_path: str) -> str | None:
        return "/nix/store/demo.drv"

    async def _requisites(_path: str) -> list[str]:
        return [str(snapshot)]

    monkeypatch.setattr(rs, "nix_store_query_deriver", _deriver)
    monkeypatch.setattr(rs, "nix_store_query_requisites", _requisites)

    plan = asyncio.run(rs.plan_snapshot_recovery(str(generation)))

    check(plan.generation == str(generation))
    check(plan.resolved_target == str(realised))
    check(plan.deriver == "/nix/store/demo.drv")
    check(plan.snapshot == str(snapshot))


def test_plan_snapshot_recovery_accepts_duplicate_identical_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow equivalent duplicate source snapshots in the closure."""
    snapshot_a = tmp_path / "snapshot-a"
    snapshot_a.mkdir()
    _write_markers(snapshot_a)
    (snapshot_a / "README.md").write_text("same\n", encoding="utf-8")

    snapshot_b = tmp_path / "snapshot-b"
    snapshot_b.mkdir()
    _write_markers(snapshot_b)
    (snapshot_b / "README.md").write_text("same\n", encoding="utf-8")

    realised = tmp_path / "realised"
    realised.write_text("out\n", encoding="utf-8")

    async def _deriver(_path: str) -> str | None:
        return "/nix/store/demo.drv"

    async def _requisites(_path: str) -> list[str]:
        return [str(snapshot_b), str(snapshot_a)]

    monkeypatch.setattr(rs, "nix_store_query_deriver", _deriver)
    monkeypatch.setattr(rs, "nix_store_query_requisites", _requisites)

    plan = asyncio.run(rs.plan_snapshot_recovery(str(realised)))

    check(plan.snapshot == str(snapshot_a))


def test_plan_snapshot_recovery_rejects_distinct_ambiguous_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject multiple matching source snapshots with different contents."""
    snapshot_a = tmp_path / "snapshot-a"
    snapshot_a.mkdir()
    _write_markers(snapshot_a)
    (snapshot_a / "README.md").write_text("first\n", encoding="utf-8")

    snapshot_b = tmp_path / "snapshot-b"
    snapshot_b.mkdir()
    _write_markers(snapshot_b)
    (snapshot_b / "README.md").write_text("second\n", encoding="utf-8")

    realised = tmp_path / "realised"
    realised.write_text("out\n", encoding="utf-8")

    async def _deriver(_path: str) -> str | None:
        return "/nix/store/demo.drv"

    async def _requisites(_path: str) -> list[str]:
        return [str(snapshot_a), str(snapshot_b)]

    monkeypatch.setattr(rs, "nix_store_query_deriver", _deriver)
    monkeypatch.setattr(rs, "nix_store_query_requisites", _requisites)

    with pytest.raises(RuntimeError, match="Multiple distinct source snapshots"):
        asyncio.run(rs.plan_snapshot_recovery(str(realised)))


def test_run_snapshot_recovery_supports_plain_and_json_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render plain snapshot output and JSON payloads."""
    plan = rs.SnapshotPlan(
        generation="/run/current-system",
        resolved_target="/nix/store/current-system",
        deriver="/nix/store/demo.drv",
        snapshot="/nix/store/demo-source",
    )

    async def _plan(_generation: str) -> rs.SnapshotPlan:
        return plan

    monkeypatch.setattr(rs, "plan_snapshot_recovery", _plan)

    check(rs.run_snapshot_recovery("/run/current-system") == 0)
    check(capsys.readouterr().out.strip() == "/nix/store/demo-source")

    check(rs.run_snapshot_recovery("/run/current-system", json_output=True) == 0)
    payload = json.loads(capsys.readouterr().out)
    check(payload["success"] is True)
    check(payload["plan"]["snapshot"] == "/nix/store/demo-source")
