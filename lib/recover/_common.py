"""Shared helpers for recovery subcommands."""

import asyncio
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lib.recover._cli import emit_error, emit_success, require_apply_for_stage

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


_NEW_FILE_MODE = 0o644


def is_git_executable(path: Path) -> bool:
    """Return whether Git would record *path* with executable mode."""
    return bool(path.stat().st_mode & stat.S_IXUSR)


def _tracked_state(path: Path) -> tuple[str, str] | tuple[str, bytes, bool] | None:
    """Return what Git tracks for *path*: symlink target, or bytes and exec bit."""
    if path.is_symlink():
        return ("symlink", path.readlink().as_posix())
    if not path.is_file():
        return None
    return ("file", path.read_bytes(), is_git_executable(path))


def files_equal(left: Path, right: Path) -> bool:
    """Return whether both paths exist with the same type, target, bytes and exec bit."""
    state = _tracked_state(left)
    return state is not None and state == _tracked_state(right)


def _restore_path(source: Path, target: Path) -> None:
    """Restore *target* atomically, keeping symlinks and the exec bit."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=target.parent, prefix=f".{target.name}.recover.", suffix=".tmp"
    ) as temp_dir:
        staged = Path(temp_dir) / "entry"
        if source.is_symlink():
            staged.symlink_to(source.readlink())
        else:
            mode = (
                target.stat().st_mode & 0o777
                if target.is_file() and not target.is_symlink()
                else _NEW_FILE_MODE
            )
            exec_bits = (mode & 0o444) >> 2 if is_git_executable(source) else 0
            with staged.open("wb") as temp_file:
                temp_file.write(source.read_bytes())
                temp_file.flush()
                os.fsync(temp_file.fileno())
            # A mode failure must leave the live target untouched.
            staged.chmod((mode & ~0o111) | exec_bits)
        staged.replace(target)


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


class RecoveryApplyError(RuntimeError):
    """A recovery failure with its completed filesystem and staging state."""

    def __init__(
        self,
        cause: BaseException,
        *,
        completed_write_paths: tuple[str, ...],
        completed_remove_paths: tuple[str, ...],
        in_flight_paths: tuple[str, ...],
        planned_count: int,
        stage_requested: bool,
        stage_attempted: bool,
    ) -> None:
        self.cause = cause
        self.completed_write_paths = completed_write_paths
        self.completed_remove_paths = completed_remove_paths
        self.completed_paths = (*completed_write_paths, *completed_remove_paths)
        self.in_flight_paths = in_flight_paths
        self.planned_count = planned_count
        self.stage_requested = stage_requested
        self.stage_attempted = stage_attempted
        self.stage_state = (
            "failed"
            if stage_attempted
            else "pending"
            if stage_requested
            else "not_requested"
        )
        in_flight = (
            f"; in flight: {', '.join(in_flight_paths)}" if in_flight_paths else ""
        )
        super().__init__(
            f"{cause} (applied {len(self.completed_paths)} of {planned_count} "
            f"planned changes before failing{in_flight}; "
            f"stage state: {self.stage_state})"
        )

    def failure_details(self) -> dict[str, object]:
        """Return JSON-serialisable partial-completion details."""
        return {
            "completed_paths": list(self.completed_paths),
            "completed_write_paths": list(self.completed_write_paths),
            "completed_remove_paths": list(self.completed_remove_paths),
            "in_flight_paths": list(self.in_flight_paths),
            "stage_state": self.stage_state,
        }


def apply_recovery(plan: RecoveryPlan, *, stage: bool = False) -> tuple[str, ...]:
    """Apply a plan in order, reporting partial completion on failure."""
    repo_root = Path(plan.repo_root)
    snapshot_root = Path(plan.snapshot)
    completed_writes: list[str] = []
    completed_removals: list[str] = []
    in_flight: str | None = None
    stage_attempted = False

    try:
        for relative_path in plan.write_paths:
            in_flight = relative_path
            _restore_path(snapshot_root / relative_path, repo_root / relative_path)
            completed_writes.append(relative_path)
            in_flight = None

        for relative_path in plan.remove_paths:
            in_flight = relative_path
            target_path = repo_root / relative_path
            if target_path.exists() or target_path.is_symlink():
                target_path.unlink()
                completed_removals.append(relative_path)
            in_flight = None

        changed_paths = (*completed_writes, *completed_removals)
        if stage:
            stage_attempted = True
            stage_paths(repo_root, changed_paths)
    except BaseException as exc:
        raise RecoveryApplyError(
            exc,
            completed_write_paths=tuple(completed_writes),
            completed_remove_paths=tuple(completed_removals),
            in_flight_paths=() if in_flight is None else (in_flight,),
            planned_count=len(plan.write_paths) + len(plan.remove_paths),
            stage_requested=stage,
            stage_attempted=stage_attempted,
        ) from exc
    return changed_paths


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
    except RecoveryApplyError as exc:
        status = emit_error(
            json_output=json_output,
            message=str(exc),
            details=exc.failure_details(),
        )
        if not isinstance(exc.cause, Exception):
            raise exc.cause from None
        return status
    except Exception as exc:  # noqa: BLE001
        return emit_error(json_output=json_output, message=str(exc))
