"""Validate higher-level update workflow structure contracts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from lib.update.ci._workflow_yaml import WorkflowObject

from lib.update.ci._workflow_analysis import (
    WorkflowAnalysis,
    WorkflowJobAnalysis,
    analyze_workflow_job,
    load_workflow_analysis,
)

_DARWIN_FULL_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
_DARWIN_LOCK_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke"
_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")
_STRUCTURE_INVALID_NEEDS_VALUE_MESSAGE = (
    "Job {job_id} defines unsupported needs {raw_needs!r}"
)
_STRUCTURE_INVALID_NEEDS_ITEM_MESSAGE = (
    "Job {job_id} defines unsupported needs {need!r}"
)
_REFRESH_WORKFLOW_JOB_IDS = ("darwin-lock-smoke", "compute-hashes")
_CERTIFY_WORKFLOW_SENTINEL_JOB_IDS = (
    "darwin-full-smoke",
    "darwin-priority-heavy",
    "darwin-extra-heavy",
    "darwin-shared",
)


def _coerce_job_analysis(
    job_id: str,
    job_data: WorkflowObject | WorkflowJobAnalysis,
) -> WorkflowJobAnalysis:
    """Normalize one raw or pre-analyzed workflow job."""
    if isinstance(job_data, WorkflowJobAnalysis):
        return job_data
    return analyze_workflow_job(
        job_id,
        job_data,
        invalid_needs_value_message=_STRUCTURE_INVALID_NEEDS_VALUE_MESSAGE,
        invalid_needs_item_message=_STRUCTURE_INVALID_NEEDS_ITEM_MESSAGE,
    )


def _coerce_workflow_analysis(
    workflow_jobs: dict[str, WorkflowObject] | dict[str, WorkflowJobAnalysis],
) -> WorkflowAnalysis:
    """Normalize one workflow job mapping for structure validation."""
    return WorkflowAnalysis.from_jobs(
        workflow_jobs,
        invalid_needs_value_message=_STRUCTURE_INVALID_NEEDS_VALUE_MESSAGE,
        invalid_needs_item_message=_STRUCTURE_INVALID_NEEDS_ITEM_MESSAGE,
    )


def _darwin_heavy_targets(
    job_data: WorkflowObject | WorkflowJobAnalysis, *, job_id: str
) -> tuple[str, ...]:
    include = _coerce_job_analysis(job_id, job_data).require_matrix_include()

    targets: list[str] = []
    packages_seen: set[str] = set()
    for entry in include:
        package = entry.get("package")
        target = entry.get("target")
        if not isinstance(package, str) or not isinstance(target, str):
            msg = f"{job_id} matrix entry must define string package/target fields: {entry!r}"
            raise TypeError(msg)
        if package in packages_seen:
            msg = f"{job_id} repeats package {package!r}"
            raise RuntimeError(msg)
        packages_seen.add(package)
        target_suffix = target.rsplit(".", 1)[-1]
        if target_suffix != package:
            msg = f"{job_id} package/target mismatch: package={package!r}, target={target!r}"
            raise RuntimeError(msg)
        targets.append(target)

    return tuple(targets)


def _darwin_split_targets(
    workflow_jobs: dict[str, WorkflowObject] | dict[str, WorkflowJobAnalysis],
    *,
    job_ids: tuple[str, ...],
) -> tuple[str, ...]:
    targets: list[str] = []
    seen_targets: dict[str, str] = {}
    workflow = _coerce_workflow_analysis(workflow_jobs)
    for job_id in job_ids:
        for target in _darwin_heavy_targets(
            workflow.require_job(job_id=job_id),
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


def _darwin_shared_exclude_refs(
    job_data: WorkflowObject | WorkflowJobAnalysis,
) -> tuple[str, ...]:
    closure_runs = [
        run
        for run in _coerce_job_analysis("darwin-shared", job_data).run_strings
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


def _validate_refresh_workflow_structure_contracts(workflow: WorkflowAnalysis) -> None:
    darwin_lock_smoke, compute_hashes = workflow.require_jobs(
        *_REFRESH_WORKFLOW_JOB_IDS
    )

    darwin_lock_smoke.require_run_marker(_DARWIN_LOCK_SMOKE_MARKER)
    darwin_lock_smoke.forbid_run_marker(_DARWIN_FULL_SMOKE_MARKER)
    darwin_lock_smoke.require_need(
        "update-lock",
        missing_need_message="darwin-lock-smoke must depend on update-lock",
    )
    darwin_lock_smoke.forbid_need(
        "merge-generated",
        forbidden_need_message="darwin-lock-smoke must stay in the lock-only phase",
    )
    compute_hashes.require_need(
        "darwin-lock-smoke",
        missing_need_message="compute-hashes must depend on darwin-lock-smoke",
    )


def _validate_certify_workflow_structure_contracts(workflow: WorkflowAnalysis) -> None:
    (
        darwin_full_smoke,
        darwin_priority_heavy,
        darwin_extra_heavy,
        darwin_shared,
        darwin_argus,
        darwin_rocinante,
        linux_x86_64,
    ) = workflow.require_jobs(
        "darwin-full-smoke",
        "darwin-priority-heavy",
        "darwin-extra-heavy",
        "darwin-shared",
        "darwin-argus",
        "darwin-rocinante",
        "linux-x86_64",
    )

    darwin_full_smoke.require_run_marker(_DARWIN_FULL_SMOKE_MARKER)

    for job in (darwin_priority_heavy, darwin_extra_heavy, darwin_shared):
        job.require_need("darwin-full-smoke")

    for job in (
        darwin_priority_heavy,
        darwin_extra_heavy,
        darwin_shared,
        darwin_argus,
        darwin_rocinante,
        linux_x86_64,
    ):
        job.forbid_need("quality-gates")

    for job in (darwin_argus, darwin_rocinante):
        job.require_need("darwin-shared")
        job.require_need("darwin-priority-heavy")
        job.forbid_need("darwin-extra-heavy")

    heavy_targets = set(
        _darwin_split_targets(
            workflow.jobs,
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
    workflow = load_workflow_analysis(
        workflow_path,
        invalid_job_message="Workflow job {job_id} must be a mapping",
        invalid_needs_value_message=_STRUCTURE_INVALID_NEEDS_VALUE_MESSAGE,
        invalid_needs_item_message=_STRUCTURE_INVALID_NEEDS_ITEM_MESSAGE,
    )

    has_refresh_jobs = workflow.has_any_job(_REFRESH_WORKFLOW_JOB_IDS)
    has_certify_jobs = workflow.has_any_job(_CERTIFY_WORKFLOW_SENTINEL_JOB_IDS)

    if not has_refresh_jobs and not has_certify_jobs:
        msg = f"Workflow {workflow_path} does not define refresh or certification update jobs"
        raise RuntimeError(msg)

    if has_refresh_jobs:
        _validate_refresh_workflow_structure_contracts(workflow)
    if has_certify_jobs:
        _validate_certify_workflow_structure_contracts(workflow)


__all__ = ["validate_workflow_structure_contracts"]
