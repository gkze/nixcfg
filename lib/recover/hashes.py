"""Recover ``flake.lock`` and ``sources.json`` from generation build inputs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import typer

from lib.recover._common import files_equal, stage_paths
from lib.recover.snapshot import DEFAULT_GENERATION, plan_snapshot_recovery
from lib.update import io as update_io
from lib.update.paths import REPO_ROOT, SOURCES_FILE_NAME, package_file_map_in


@dataclass(frozen=True)
class HashRecoveryPlan:
    """One recovery plan for tracked hash files."""

    generation: str
    resolved_target: str
    deriver: str
    snapshot: str
    repo_root: str
    write_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation."""
        return asdict(self)


def _managed_relative_paths(root: Path) -> set[str]:
    """Return managed hash file paths relative to *root*."""
    paths = {"flake.lock"}
    for path in package_file_map_in(root, SOURCES_FILE_NAME).values():
        paths.add(path.relative_to(root).as_posix())
    return paths


async def plan_hash_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    repo_root: Path = REPO_ROOT,
    sync: bool = False,
) -> HashRecoveryPlan:
    """Build a recovery plan for ``flake.lock`` and ``sources.json`` files."""
    snapshot_plan = await plan_snapshot_recovery(generation)
    snapshot_root = Path(snapshot_plan.snapshot)

    snapshot_paths = _managed_relative_paths(snapshot_root)
    current_paths = _managed_relative_paths(repo_root)

    write_paths = tuple(
        relative_path
        for relative_path in sorted(snapshot_paths)
        if not files_equal(snapshot_root / relative_path, repo_root / relative_path)
    )
    remove_paths = tuple(sorted(current_paths - snapshot_paths)) if sync else ()

    return HashRecoveryPlan(
        generation=snapshot_plan.generation,
        resolved_target=snapshot_plan.resolved_target,
        deriver=snapshot_plan.deriver,
        snapshot=snapshot_plan.snapshot,
        repo_root=str(repo_root),
        write_paths=write_paths,
        remove_paths=remove_paths,
    )


def apply_hash_recovery(
    plan: HashRecoveryPlan,
    *,
    stage: bool = False,
) -> tuple[str, ...]:
    """Apply a hash recovery plan and optionally stage the changed files."""
    repo_root = Path(plan.repo_root)
    snapshot_root = Path(plan.snapshot)
    changed_paths: list[str] = []

    for relative_path in plan.write_paths:
        source_path = snapshot_root / relative_path
        target_path = repo_root / relative_path
        update_io.atomic_write_bytes(target_path, source_path.read_bytes(), mkdir=True)
        changed_paths.append(relative_path)

    for relative_path in plan.remove_paths:
        target_path = repo_root / relative_path
        if target_path.exists():
            target_path.unlink()
            changed_paths.append(relative_path)

    changed_tuple = tuple(changed_paths)
    if stage:
        stage_paths(repo_root, changed_tuple)
    return changed_tuple


def _render_plain(
    plan: HashRecoveryPlan,
    *,
    apply: bool,
    stage: bool,
    sync: bool,
    changed_paths: tuple[str, ...] | None = None,
) -> str:
    """Render a human-readable summary of the recovery plan/result."""
    lines = [
        f"Generation: {plan.generation}",
        f"Resolved path: {plan.resolved_target}",
        f"Deriver: {plan.deriver}",
        f"Source snapshot: {plan.snapshot}",
        f"Repo root: {plan.repo_root}",
        f"Sync mode: {'on' if sync else 'off'}",
    ]

    action = "Will restore" if not apply else "Restored"
    if plan.write_paths:
        lines.append(f"{action} ({len(plan.write_paths)}):")
        lines.extend(f"  {path}" for path in plan.write_paths)
    else:
        lines.append(f"{action}: none")

    remove_action = "Will remove" if not apply else "Removed"
    if plan.remove_paths:
        lines.append(f"{remove_action} ({len(plan.remove_paths)}):")
        lines.extend(f"  {path}" for path in plan.remove_paths)
    elif sync:
        lines.append(f"{remove_action}: none")

    if apply:
        changed = changed_paths or ()
        lines.append(f"Applied changes: {len(changed)}")
        if stage:
            lines.append("Staged changes: yes")
    return "\n".join(lines)


def run_hash_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    apply: bool = False,
    json_output: bool = False,
    stage: bool = False,
    sync: bool = False,
) -> int:
    """Plan or apply hash recovery for a realised generation."""
    if stage and not apply:
        message = "--stage requires --apply"
        if json_output:
            typer.echo(json.dumps({"success": False, "error": message}))
        else:
            typer.echo(f"Error: {message}", err=True)
        return 1

    try:
        plan = asyncio.run(plan_hash_recovery(generation, sync=sync))
        changed_paths: tuple[str, ...] | None = None
        if apply:
            changed_paths = apply_hash_recovery(plan, stage=stage)

        payload = {
            "success": True,
            "apply": apply,
            "stage": stage,
            "sync": sync,
            "changed_paths": list(changed_paths or ()),
            "plan": plan.to_dict(),
        }
        if json_output:
            typer.echo(json.dumps(payload))
        else:
            typer.echo(
                _render_plain(
                    plan,
                    apply=apply,
                    stage=stage,
                    sync=sync,
                    changed_paths=changed_paths,
                )
            )
    except Exception as exc:  # noqa: BLE001
        if json_output:
            typer.echo(json.dumps({"success": False, "error": str(exc)}))
        else:
            typer.echo(f"Error: {exc}", err=True)
        return 1
    else:
        return 0
