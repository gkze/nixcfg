"""Recover arbitrary repo files from realised generation source snapshots."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib.recover._common import (
    RecoveryPlan,
    apply_recovery,
    compute_recovery_paths,
    run_recovery,
)
from lib.recover.snapshot import DEFAULT_GENERATION, plan_snapshot_recovery
from lib.update.paths import REPO_ROOT


@dataclass(frozen=True)
class FileRecoveryPlan(RecoveryPlan):
    """Recovery plan for explicitly selected repo files."""

    path_selectors: tuple[str, ...] = ()
    glob_selectors: tuple[str, ...] = ()

    def selector_sections(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return the labelled selector lists this plan reports."""
        return (
            ("Path selectors", self.path_selectors),
            ("Glob selectors", self.glob_selectors),
        )


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
    if not (snapshot_matches | current_matches):
        msg = "No files matched the requested selectors"
        raise RuntimeError(msg)

    write_paths, remove_paths = compute_recovery_paths(
        snapshot_root=snapshot_root,
        repo_root=repo_root,
        snapshot_paths=snapshot_matches,
        current_paths=current_matches,
        sync=sync,
    )

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
    return apply_recovery(plan, stage=stage)


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
    return run_recovery(
        plan_fn=lambda: plan_file_recovery(
            generation,
            globs=globs,
            paths=paths,
            sync=sync,
        ),
        apply_fn=apply_file_recovery,
        apply=apply,
        json_output=json_output,
        stage=stage,
        sync=sync,
    )
