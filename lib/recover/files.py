"""Recover arbitrary repo files from realised generation source snapshots."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from lib.recover._cli import emit_error, emit_success, require_apply_for_stage
from lib.recover._common import files_equal, stage_paths
from lib.recover.snapshot import DEFAULT_GENERATION, plan_snapshot_recovery
from lib.update import io as update_io
from lib.update.paths import REPO_ROOT


@dataclass(frozen=True)
class FileRecoveryPlan:
    """Recovery plan for explicitly selected repo files."""

    generation: str
    resolved_target: str
    deriver: str
    snapshot: str
    repo_root: str
    path_selectors: tuple[str, ...]
    glob_selectors: tuple[str, ...]
    write_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation."""
        return asdict(self)


def _normalise_selector(selector: str, *, kind: str) -> str:
    """Validate and normalise one repo-relative selector."""
    stripped = selector.strip()
    if not stripped:
        msg = f"{kind} selector must not be empty"
        raise RuntimeError(msg)
    pure = PurePosixPath(stripped)
    if pure.is_absolute():
        msg = f"{kind} selector must be repo-relative: {selector}"
        raise RuntimeError(msg)
    if any(part == ".." for part in pure.parts):
        msg = f"{kind} selector must not escape the repo root: {selector}"
        raise RuntimeError(msg)
    if pure.as_posix() == ".":
        msg = f"{kind} selector must point below the repo root"
        raise RuntimeError(msg)
    return pure.as_posix()


def _normalise_selectors(
    *,
    paths: tuple[str, ...],
    globs: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate and deduplicate selector inputs."""
    normalised_paths = tuple(
        dict.fromkeys(_normalise_selector(path, kind="path") for path in paths)
    )
    normalised_globs = tuple(
        dict.fromkeys(_normalise_selector(glob, kind="glob") for glob in globs)
    )
    if not normalised_paths and not normalised_globs:
        msg = "At least one --path or --glob selector is required"
        raise RuntimeError(msg)
    return normalised_paths, normalised_globs


def _match_explicit_paths(root: Path, paths: tuple[str, ...]) -> set[str]:
    """Return explicit selector matches under *root*."""
    matches: set[str] = set()
    for relative_path in paths:
        candidate = root / relative_path
        if candidate.is_file():
            matches.add(relative_path)
    return matches


def _match_globs(root: Path, globs: tuple[str, ...]) -> set[str]:
    """Return repo-relative file matches for glob selectors under *root*."""
    matches: set[str] = set()
    for pattern in globs:
        for candidate in root.glob(pattern):
            if candidate.is_file():
                matches.add(candidate.relative_to(root).as_posix())
    return matches


async def plan_file_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    globs: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
    repo_root: Path = REPO_ROOT,
    sync: bool = False,
) -> FileRecoveryPlan:
    """Build a recovery plan for selected repo files."""
    path_selectors, glob_selectors = _normalise_selectors(paths=paths, globs=globs)
    snapshot_plan = await plan_snapshot_recovery(generation)
    snapshot_root = Path(snapshot_plan.snapshot)

    snapshot_matches = _match_explicit_paths(
        snapshot_root, path_selectors
    ) | _match_globs(
        snapshot_root,
        glob_selectors,
    )
    current_matches = _match_explicit_paths(repo_root, path_selectors) | _match_globs(
        repo_root,
        glob_selectors,
    )
    selected_paths = snapshot_matches | current_matches
    if not selected_paths:
        msg = "No files matched the requested selectors"
        raise RuntimeError(msg)

    write_paths = tuple(
        relative_path
        for relative_path in sorted(snapshot_matches)
        if not files_equal(snapshot_root / relative_path, repo_root / relative_path)
    )
    remove_paths = tuple(sorted(current_matches - snapshot_matches)) if sync else ()

    return FileRecoveryPlan(
        generation=snapshot_plan.generation,
        resolved_target=snapshot_plan.resolved_target,
        deriver=snapshot_plan.deriver,
        snapshot=snapshot_plan.snapshot,
        repo_root=str(repo_root),
        path_selectors=path_selectors,
        glob_selectors=glob_selectors,
        write_paths=write_paths,
        remove_paths=remove_paths,
    )


def apply_file_recovery(
    plan: FileRecoveryPlan,
    *,
    stage: bool = False,
) -> tuple[str, ...]:
    """Apply a file recovery plan and optionally stage the changes."""
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
    plan: FileRecoveryPlan,
    *,
    apply: bool,
    stage: bool,
    sync: bool,
    changed_paths: tuple[str, ...] | None = None,
) -> str:
    """Render a human-readable summary of the file recovery plan/result."""
    lines = [
        f"Generation: {plan.generation}",
        f"Resolved path: {plan.resolved_target}",
        f"Deriver: {plan.deriver}",
        f"Source snapshot: {plan.snapshot}",
        f"Repo root: {plan.repo_root}",
        f"Sync mode: {'on' if sync else 'off'}",
    ]
    if plan.path_selectors:
        lines.append(f"Path selectors ({len(plan.path_selectors)}):")
        lines.extend(f"  {selector}" for selector in plan.path_selectors)
    if plan.glob_selectors:
        lines.append(f"Glob selectors ({len(plan.glob_selectors)}):")
        lines.extend(f"  {selector}" for selector in plan.glob_selectors)

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


def run_file_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    apply: bool = False,
    globs: tuple[str, ...] = (),
    json_output: bool = False,
    paths: tuple[str, ...] = (),
    stage: bool = False,
    sync: bool = False,
) -> int:
    """Plan or apply recovery for selected repo files."""
    stage_validation = require_apply_for_stage(
        apply=apply,
        json_output=json_output,
        stage=stage,
    )
    if stage_validation is not None:
        return stage_validation

    try:
        plan = asyncio.run(
            plan_file_recovery(
                generation,
                globs=globs,
                paths=paths,
                sync=sync,
            )
        )
        changed_paths: tuple[str, ...] | None = None
        if apply:
            changed_paths = apply_file_recovery(plan, stage=stage)

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
            plain=_render_plain(
                plan,
                apply=apply,
                stage=stage,
                sync=sync,
                changed_paths=changed_paths,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return emit_error(json_output=json_output, message=str(exc))
