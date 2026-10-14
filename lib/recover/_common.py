"""Shared helpers for recovery subcommands."""

import asyncio
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.recover._cli import emit_error, emit_success, require_apply_for_stage
from lib.update import io as update_io

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True)
class RecoveryPlan:
    """The plan fields and projections every recovery subcommand shares.

    Both concrete plans subclass this and add only their own selection inputs,
    so the two commands cannot drift in how they restore, stage or report.
    """

    generation: str
    resolved_target: str
    deriver: str
    snapshot: str
    repo_root: str
    write_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation of this plan."""
        return asdict(self)

    def selector_sections(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return labelled selector lists for the human-readable summary."""
        return ()


def files_equal(left: Path, right: Path) -> bool:
    """Return whether two files exist and have identical contents."""
    if not left.exists() or not right.exists():
        return False
    return left.read_bytes() == right.read_bytes()


def stage_paths(repo_root: Path, paths: tuple[str, ...]) -> None:
    """Stage *paths* in Git, including deletions."""
    if not paths:
        return
    git_bin = shutil.which("git")
    if git_bin is None:
        msg = "git not found on PATH"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [git_bin, "add", "-A", "--", *paths],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "git add failed"
        raise RuntimeError(stderr)


def compute_recovery_paths(
    *,
    snapshot_root: Path,
    repo_root: Path,
    snapshot_paths: set[str],
    current_paths: set[str],
    sync: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the paths to restore and, in sync mode, the local-only ones to remove.

    A path is restored only when the snapshot copy differs from the checkout;
    sync removal is the set difference in the other direction. Both recovery
    commands select their own paths, but this write/remove rule is single-owned.
    """
    write_paths = tuple(
        relative_path
        for relative_path in sorted(snapshot_paths)
        if not files_equal(snapshot_root / relative_path, repo_root / relative_path)
    )
    remove_paths = tuple(sorted(current_paths - snapshot_paths)) if sync else ()
    return write_paths, remove_paths


def apply_recovery(plan: RecoveryPlan, *, stage: bool = False) -> tuple[str, ...]:
    """Restore and remove a recovery plan's paths, optionally staging them."""
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


def render_plan(
    plan: RecoveryPlan,
    *,
    apply: bool,
    stage: bool,
    sync: bool,
    changed_paths: tuple[str, ...] | None = None,
) -> str:
    """Render the plain-text plan/result summary every recovery command shares."""
    lines = [
        f"Generation: {plan.generation}",
        f"Resolved path: {plan.resolved_target}",
        f"Deriver: {plan.deriver}",
        f"Source snapshot: {plan.snapshot}",
        f"Repo root: {plan.repo_root}",
        f"Sync mode: {'on' if sync else 'off'}",
    ]
    for label, values in plan.selector_sections():
        if values:
            lines.append(f"{label} ({len(values)}):")
            lines.extend(f"  {value}" for value in values)

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


def run_recovery(
    *,
    plan_fn: Callable[[], Awaitable[RecoveryPlan]],
    apply_fn: Callable[..., tuple[str, ...]],
    apply: bool,
    json_output: bool,
    stage: bool,
    sync: bool,
) -> int:
    """Plan or apply a recovery, rendering success and failure through the CLI."""
    stage_validation = require_apply_for_stage(
        apply=apply,
        json_output=json_output,
        stage=stage,
    )
    if stage_validation is not None:
        return stage_validation

    try:
        plan = asyncio.run(plan_fn())
        changed_paths: tuple[str, ...] | None = None
        if apply:
            changed_paths = apply_fn(plan, stage=stage)

        payload = {
            "success": True,
            "apply": apply,
            "stage": stage,
            "sync": sync,
            "changed_paths": list(changed_paths or ()),
            "plan": plan.to_dict(),
        }
        return emit_success(
            json_output=json_output,
            payload=payload,
            plain=render_plan(
                plan,
                apply=apply,
                stage=stage,
                sync=sync,
                changed_paths=changed_paths,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return emit_error(json_output=json_output, message=str(exc))
