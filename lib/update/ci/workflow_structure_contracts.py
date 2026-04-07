"""Validate higher-level update workflow structure contracts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from lib import json_utils

type WorkflowValue = json_utils.JsonValue
type WorkflowObject = json_utils.JsonObject

_DARWIN_FULL_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
_DARWIN_LOCK_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke"
_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")


def _stringify_yaml_keys(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _stringify_yaml_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_stringify_yaml_keys(item) for item in value]
    return value


def _workflow_object(value: object, *, context: str) -> WorkflowObject:
    return json_utils.coerce_json_object(_stringify_yaml_keys(value), context=context)


def _load_jobs(workflow_path: Path) -> dict[str, WorkflowObject]:
    loaded = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = f"Workflow {workflow_path} did not parse to a mapping"
        raise TypeError(msg)
    payload = _workflow_object(loaded, context=f"workflow {workflow_path}")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        msg = f"Workflow {workflow_path} is missing a top-level jobs mapping"
        raise TypeError(msg)
    workflow_jobs: dict[str, WorkflowObject] = {}
    for job_id, job_data in jobs.items():
        if not isinstance(job_data, dict):
            msg = f"Workflow job {job_id} must be a mapping"
            raise TypeError(msg)
        workflow_jobs[job_id] = _workflow_object(
            job_data, context=f"workflow job {job_id}"
        )
    return workflow_jobs


def _require_job(
    workflow_jobs: dict[str, WorkflowObject], *, job_id: str
) -> WorkflowObject:
    try:
        return workflow_jobs[job_id]
    except KeyError as exc:
        msg = f"Workflow is missing required job {job_id!r}"
        raise RuntimeError(msg) from exc


def _parse_job_needs(job_data: WorkflowObject, *, job_id: str) -> tuple[str, ...]:
    raw_needs = job_data.get("needs")
    if raw_needs is None:
        return ()
    if isinstance(raw_needs, str):
        return (raw_needs,)
    if isinstance(raw_needs, list) and all(isinstance(need, str) for need in raw_needs):
        return tuple(raw_needs)
    msg = f"Job {job_id} defines unsupported needs {raw_needs!r}"
    raise TypeError(msg)


def _job_run_steps(job_data: WorkflowObject, *, job_id: str) -> tuple[str, ...]:
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


def _require_job_run(job_data: WorkflowObject, *, job_id: str, marker: str) -> None:
    if not any(marker in run for run in _job_run_steps(job_data, job_id=job_id)):
        msg = f"Job {job_id} is missing required run step containing {marker!r}"
        raise RuntimeError(msg)


def _forbid_job_run(job_data: WorkflowObject, *, job_id: str, marker: str) -> None:
    if any(marker in run for run in _job_run_steps(job_data, job_id=job_id)):
        msg = f"Job {job_id} must not run step containing {marker!r}"
        raise RuntimeError(msg)


def _require_job_need(job_data: WorkflowObject, *, job_id: str, need: str) -> None:
    if need not in _parse_job_needs(job_data, job_id=job_id):
        msg = f"{job_id} must depend on {need}"
        raise RuntimeError(msg)


def _forbid_job_need(job_data: WorkflowObject, *, job_id: str, need: str) -> None:
    if need in _parse_job_needs(job_data, job_id=job_id):
        msg = f"{job_id} must not depend on {need}"
        raise RuntimeError(msg)


def _darwin_heavy_targets(job_data: WorkflowObject, *, job_id: str) -> tuple[str, ...]:
    strategy = job_data.get("strategy")
    if not isinstance(strategy, dict):
        msg = f"{job_id} does not define a strategy mapping"
        raise TypeError(msg)
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        msg = f"{job_id} does not define a matrix mapping"
        raise TypeError(msg)
    include = matrix.get("include")
    if not isinstance(include, list) or not include:
        msg = f"{job_id} matrix.include must be a non-empty list"
        raise TypeError(msg)

    targets: list[str] = []
    packages_seen: set[str] = set()
    for entry in include:
        if not isinstance(entry, dict):
            msg = f"Unsupported {job_id} matrix entry: {entry!r}"
            raise TypeError(msg)
        package = entry.get("package")
        target = entry.get("target")
        if not isinstance(package, str) or not isinstance(target, str):
            msg = (
                f"{job_id} matrix entry must define string "
                f"package/target fields: {entry!r}"
            )
            raise TypeError(msg)
        if package in packages_seen:
            msg = f"{job_id} repeats package {package!r}"
            raise RuntimeError(msg)
        packages_seen.add(package)
        target_suffix = target.rsplit(".", 1)[-1]
        if target_suffix != package:
            msg = (
                f"{job_id} package/target mismatch: "
                f"package={package!r}, target={target!r}"
            )
            raise RuntimeError(msg)
        targets.append(target)

    return tuple(targets)


def _darwin_split_targets(
    workflow_jobs: dict[str, WorkflowObject], *, job_ids: tuple[str, ...]
) -> tuple[str, ...]:
    targets: list[str] = []
    seen_targets: dict[str, str] = {}
    for job_id in job_ids:
        for target in _darwin_heavy_targets(
            _require_job(workflow_jobs, job_id=job_id),
            job_id=job_id,
        ):
            previous_job = seen_targets.get(target)
            if previous_job is not None:
                msg = (
                    f"{job_id} repeats heavy target {target!r} already declared by "
                    f"{previous_job!r}"
                )
                raise RuntimeError(msg)
            seen_targets[target] = job_id
            targets.append(target)
    return tuple(targets)


def _darwin_shared_exclude_refs(job_data: WorkflowObject) -> tuple[str, ...]:
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


def _validate_refresh_workflow_structure_contracts(
    workflow_jobs: dict[str, WorkflowObject],
) -> None:
    darwin_lock_smoke = _require_job(workflow_jobs, job_id="darwin-lock-smoke")
    compute_hashes = _require_job(workflow_jobs, job_id="compute-hashes")

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
    if "darwin-lock-smoke" not in _parse_job_needs(
        compute_hashes,
        job_id="compute-hashes",
    ):
        msg = "compute-hashes must depend on darwin-lock-smoke"
        raise RuntimeError(msg)


def _validate_certify_workflow_structure_contracts(
    workflow_jobs: dict[str, WorkflowObject],
) -> None:
    darwin_full_smoke = _require_job(workflow_jobs, job_id="darwin-full-smoke")
    darwin_priority_heavy = _require_job(workflow_jobs, job_id="darwin-priority-heavy")
    darwin_extra_heavy = _require_job(workflow_jobs, job_id="darwin-extra-heavy")
    darwin_shared = _require_job(workflow_jobs, job_id="darwin-shared")
    darwin_argus = _require_job(workflow_jobs, job_id="darwin-argus")
    darwin_rocinante = _require_job(workflow_jobs, job_id="darwin-rocinante")
    linux_x86_64 = _require_job(workflow_jobs, job_id="linux-x86_64")

    _require_job_run(
        darwin_full_smoke,
        job_id="darwin-full-smoke",
        marker=_DARWIN_FULL_SMOKE_MARKER,
    )

    for job_id, job_data in (
        ("darwin-priority-heavy", darwin_priority_heavy),
        ("darwin-extra-heavy", darwin_extra_heavy),
        ("darwin-shared", darwin_shared),
    ):
        _require_job_need(job_data, job_id=job_id, need="darwin-full-smoke")

    for job_id, job_data in (
        ("darwin-priority-heavy", darwin_priority_heavy),
        ("darwin-extra-heavy", darwin_extra_heavy),
        ("darwin-shared", darwin_shared),
        ("darwin-argus", darwin_argus),
        ("darwin-rocinante", darwin_rocinante),
        ("linux-x86_64", linux_x86_64),
    ):
        _forbid_job_need(job_data, job_id=job_id, need="quality-gates")

    for job_id, job_data in (
        ("darwin-argus", darwin_argus),
        ("darwin-rocinante", darwin_rocinante),
    ):
        _require_job_need(job_data, job_id=job_id, need="darwin-shared")
        _require_job_need(job_data, job_id=job_id, need="darwin-priority-heavy")
        _forbid_job_need(job_data, job_id=job_id, need="darwin-extra-heavy")

    heavy_targets = set(
        _darwin_split_targets(
            workflow_jobs,
            job_ids=("darwin-priority-heavy", "darwin-extra-heavy"),
        )
    )
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


def validate_workflow_structure_contracts(*, workflow_path: Path) -> None:
    """Validate refresh/certification structure contracts in one workflow file."""
    jobs = _load_jobs(workflow_path)

    has_refresh_jobs = any(
        job_id in jobs for job_id in ("darwin-lock-smoke", "compute-hashes")
    )
    has_certify_jobs = any(
        job_id in jobs
        for job_id in (
            "darwin-full-smoke",
            "darwin-priority-heavy",
            "darwin-extra-heavy",
            "darwin-shared",
        )
    )

    if not has_refresh_jobs and not has_certify_jobs:
        msg = (
            f"Workflow {workflow_path} does not define refresh or certification "
            "update jobs"
        )
        raise RuntimeError(msg)

    if has_refresh_jobs:
        _validate_refresh_workflow_structure_contracts(jobs)
    if has_certify_jobs:
        _validate_certify_workflow_structure_contracts(jobs)


__all__ = ["validate_workflow_structure_contracts"]
