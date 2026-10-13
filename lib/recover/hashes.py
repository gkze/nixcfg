"""Recover ``flake.lock`` and ``sources.json`` from generation build inputs."""

from dataclasses import dataclass
from pathlib import Path

from lib.recover._common import (
    RecoveryPlan,
    apply_recovery,
    compute_recovery_paths,
    run_recovery,
)
from lib.recover.snapshot import DEFAULT_GENERATION, plan_snapshot_recovery
from lib.update.paths import REPO_ROOT, SOURCES_FILE_NAME, package_file_map_in


@dataclass(frozen=True)
class HashRecoveryPlan(RecoveryPlan):
    """One recovery plan for tracked hash files."""


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

    write_paths, remove_paths = compute_recovery_paths(
        snapshot_root=snapshot_root,
        repo_root=repo_root,
        snapshot_paths=snapshot_paths,
        current_paths=current_paths,
        sync=sync,
    )

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
    return apply_recovery(plan, stage=stage)


def run_hash_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    apply: bool = False,
    json_output: bool = False,
    stage: bool = False,
    sync: bool = False,
) -> int:
    """Plan or apply hash recovery for a realised generation."""
    return run_recovery(
        plan_fn=lambda: plan_hash_recovery(generation, sync=sync),
        apply_fn=apply_hash_recovery,
        apply=apply,
        json_output=json_output,
        stage=stage,
        sync=sync,
    )
