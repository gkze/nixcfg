"""Validate higher-level update workflow structure contracts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

import yaml

_DARWIN_FULL_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
_DARWIN_LOCK_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke"
_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")


def _load_jobs(workflow_path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Workflow {workflow_path} did not parse to a mapping"
        raise TypeError(msg)
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        msg = f"Workflow {workflow_path} is missing a top-level jobs mapping"
        raise TypeError(msg)
    return {
        str(job_id): cast("dict[str, Any]", job_data)
        for job_id, job_data in jobs.items()
        if isinstance(job_data, dict)
    }


def _require_job(
    workflow_jobs: dict[str, dict[str, Any]], *, job_id: str
) -> dict[str, Any]:
    try:
        return workflow_jobs[job_id]
    except KeyError as exc:
        msg = f"Workflow is missing required job {job_id!r}"
        raise RuntimeError(msg) from exc


def _parse_job_needs(job_data: dict[str, Any], *, job_id: str) -> tuple[str, ...]:
    raw_needs = job_data.get("needs")
    if raw_needs is None:
        return ()
    if isinstance(raw_needs, str):
        return (raw_needs,)
    if isinstance(raw_needs, list) and all(isinstance(need, str) for need in raw_needs):
        return tuple(raw_needs)
    msg = f"Job {job_id} defines unsupported needs {raw_needs!r}"
    raise TypeError(msg)


def _job_run_steps(job_data: dict[str, Any], *, job_id: str) -> tuple[str, ...]:
    raw_steps = job_data.get("steps")
    if not isinstance(raw_steps, list):
        msg = f"Job {job_id} does not define steps as a list"
        raise TypeError(msg)

    runs: list[str] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str):
            runs.append(run)
    return tuple(runs)


def _require_job_run(job_data: dict[str, Any], *, job_id: str, marker: str) -> None:
    if not any(marker in run for run in _job_run_steps(job_data, job_id=job_id)):
        msg = f"Job {job_id} is missing required run step containing {marker!r}"
        raise RuntimeError(msg)


def _forbid_job_run(job_data: dict[str, Any], *, job_id: str, marker: str) -> None:
    if any(marker in run for run in _job_run_steps(job_data, job_id=job_id)):
        msg = f"Job {job_id} must not run step containing {marker!r}"
        raise RuntimeError(msg)


def _darwin_shared_heavy_targets(job_data: dict[str, Any]) -> tuple[str, ...]:
    strategy = job_data.get("strategy")
    if not isinstance(strategy, dict):
        msg = "darwin-shared-heavy does not define a strategy mapping"
        raise TypeError(msg)
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        msg = "darwin-shared-heavy does not define a matrix mapping"
        raise TypeError(msg)
    include = matrix.get("include")
    if not isinstance(include, list) or not include:
        msg = "darwin-shared-heavy matrix.include must be a non-empty list"
        raise TypeError(msg)

    targets: list[str] = []
    packages_seen: set[str] = set()
    for entry in include:
        if not isinstance(entry, dict):
            msg = f"Unsupported darwin-shared-heavy matrix entry: {entry!r}"
            raise TypeError(msg)
        package = entry.get("package")
        target = entry.get("target")
        if not isinstance(package, str) or not isinstance(target, str):
            msg = (
                "darwin-shared-heavy matrix entry must define string "
                f"package/target fields: {entry!r}"
            )
            raise TypeError(msg)
        if package in packages_seen:
            msg = f"darwin-shared-heavy repeats package {package!r}"
            raise RuntimeError(msg)
        packages_seen.add(package)
        target_suffix = target.rsplit(".", 1)[-1]
        if target_suffix != package:
            msg = (
                "darwin-shared-heavy package/target mismatch: "
                f"package={package!r}, target={target!r}"
            )
            raise RuntimeError(msg)
        targets.append(target)

    return tuple(targets)


def _darwin_shared_exclude_refs(job_data: dict[str, Any]) -> tuple[str, ...]:
    closure_runs = [
        run
        for run in _job_run_steps(job_data, job_id="darwin-shared")
        if _SHARED_CLOSURE_MARKER in run
    ]
    if not closure_runs:
        msg = "darwin-shared is missing the shared Darwin closure build step"
        raise RuntimeError(msg)
    if len(closure_runs) != 1:
        msg = "darwin-shared defines multiple shared Darwin closure build steps"
        raise RuntimeError(msg)

    refs = [match.strip("\"'") for match in _EXCLUDE_REF_RE.findall(closure_runs[0])]
    if not refs:
        msg = "darwin-shared closure step does not define any --exclude-ref targets"
        raise RuntimeError(msg)
    if len(set(refs)) != len(refs):
        msg = "darwin-shared closure step repeats one or more --exclude-ref targets"
        raise RuntimeError(msg)
    return tuple(refs)


def validate_workflow_structure_contracts(*, workflow_path: Path) -> None:
    """Validate higher-level structure contracts in one workflow file."""
    jobs = _load_jobs(workflow_path)

    darwin_lock_smoke = _require_job(jobs, job_id="darwin-lock-smoke")
    darwin_full_smoke = _require_job(jobs, job_id="darwin-full-smoke")
    compute_hashes = _require_job(jobs, job_id="compute-hashes")
    darwin_shared_heavy = _require_job(jobs, job_id="darwin-shared-heavy")
    darwin_shared = _require_job(jobs, job_id="darwin-shared")

    _require_job_run(
        darwin_lock_smoke,
        job_id="darwin-lock-smoke",
        marker=_DARWIN_LOCK_SMOKE_MARKER,
    )
    _forbid_job_run(
        darwin_lock_smoke,
        job_id="darwin-lock-smoke",
        marker=_DARWIN_FULL_SMOKE_MARKER,
    )
    _require_job_run(
        darwin_full_smoke,
        job_id="darwin-full-smoke",
        marker=_DARWIN_FULL_SMOKE_MARKER,
    )

    if "update-lock" not in _parse_job_needs(
        darwin_lock_smoke, job_id="darwin-lock-smoke"
    ):
        msg = "darwin-lock-smoke must depend on update-lock"
        raise RuntimeError(msg)
    if "merge-generated" in _parse_job_needs(
        darwin_lock_smoke,
        job_id="darwin-lock-smoke",
    ):
        msg = "darwin-lock-smoke must stay in the lock-only phase"
        raise RuntimeError(msg)
    if "merge-generated" not in _parse_job_needs(
        darwin_full_smoke,
        job_id="darwin-full-smoke",
    ):
        msg = "darwin-full-smoke must depend on merge-generated"
        raise RuntimeError(msg)
    if "darwin-lock-smoke" not in _parse_job_needs(
        compute_hashes,
        job_id="compute-hashes",
    ):
        msg = "compute-hashes must depend on darwin-lock-smoke"
        raise RuntimeError(msg)
    if "darwin-full-smoke" not in _parse_job_needs(
        darwin_shared_heavy,
        job_id="darwin-shared-heavy",
    ):
        msg = "darwin-shared-heavy must depend on darwin-full-smoke"
        raise RuntimeError(msg)
    if "darwin-full-smoke" not in _parse_job_needs(
        darwin_shared,
        job_id="darwin-shared",
    ):
        msg = "darwin-shared must depend on darwin-full-smoke"
        raise RuntimeError(msg)

    heavy_targets = set(_darwin_shared_heavy_targets(darwin_shared_heavy))
    excluded_targets = set(_darwin_shared_exclude_refs(darwin_shared))

    if heavy_targets != excluded_targets:
        missing = sorted(heavy_targets - excluded_targets)
        extra = sorted(excluded_targets - heavy_targets)
        problems: list[str] = []
        if missing:
            problems.append(f"missing excludes: {', '.join(missing)}")
        if extra:
            problems.append(f"unexpected excludes: {', '.join(extra)}")
        msg = "Darwin heavy-target split drift detected (" + "; ".join(problems) + ")"
        raise RuntimeError(msg)


__all__ = ["validate_workflow_structure_contracts"]
