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
from lib.update.ci._workflow_yaml import load_workflow_yaml

_DARWIN_FULL_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
_DARWIN_LOCK_SMOKE_MARKER = "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke"
_SHARED_CLOSURE_MARKER = "nix run .#nixcfg -- ci cache closure"
_DISPATCH_CI_MARKER = "gh workflow run ci.yml"
_DISPATCH_CERTIFY_MARKER = "gh workflow run update-certify.yml"
_AGGREGATE_DISCOVER_SUCCESS_MARKER = "needs.discover-update-targets.result == 'success'"
_CERTIFICATION_HEAD_SHA_MARKER = "WORKFLOW_RUN_HEAD_SHA"
_CERTIFICATION_ANCESTRY_MARKER = "merge-base --is-ancestor"
_CERTIFICATION_JOBS_API_MARKER = "/actions/runs/${{ github.run_id }}/jobs?per_page=100"
_CERTIFICATION_JOBS_JSON_MARKER = "--jobs-json /tmp/certification-jobs.json"
_UPDATE_GITHUB_ENV_VALUE = "${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}"
_UPDATE_API_TOKEN_RUN_MARKERS = (
    "nix run .#nixcfg -- ci pipeline versions",
    "nix run .#nixcfg -- update --list --json",
    "nix run .#nixcfg -- update --native-only",
)
_CHECKOUT_ACTION_PREFIX = "actions/checkout@"
_EXCLUDE_REF_RE = re.compile(r"--exclude-ref\s+([^\s\\]+)")
_STRUCTURE_INVALID_NEEDS_VALUE_MESSAGE = (
    "Job {job_id} defines unsupported needs {raw_needs!r}"
)
_STRUCTURE_INVALID_NEEDS_ITEM_MESSAGE = (
    "Job {job_id} defines unsupported needs {need!r}"
)
_REFRESH_WORKFLOW_JOB_IDS = (
    "darwin-lock-smoke",
    "discover-update-targets",
    "compute-hashes-aarch64-darwin",
    "compute-hashes-x86_64-linux",
    "compute-hashes-aarch64-linux",
    "aggregate-platform-updates",
)
_REFRESH_CONCURRENCY_CANCEL_MESSAGE = (
    "Update refresh workflow must set concurrency.cancel-in-progress: false "
    "so scheduled refreshes do not cancel in-flight package slices"
)
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
    (
        darwin_lock_smoke,
        discover_update_targets,
        compute_hashes_darwin,
        compute_hashes_x86_64_linux,
        compute_hashes_aarch64_linux,
        aggregate_platform_updates,
        create_pr,
    ) = workflow.require_jobs(
        *_REFRESH_WORKFLOW_JOB_IDS,
        "create-pr",
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
    discover_update_targets.require_need(
        "update-lock",
        missing_need_message="discover-update-targets must depend on update-lock",
    )
    discover_update_targets.require_need(
        "resolve-versions",
        missing_need_message="discover-update-targets must depend on resolve-versions",
    )
    for compute_hashes in (
        compute_hashes_darwin,
        compute_hashes_x86_64_linux,
        compute_hashes_aarch64_linux,
    ):
        compute_hashes.require_need(
            "discover-update-targets",
            missing_need_message=("{job_id} must depend on discover-update-targets"),
        )
        compute_hashes.require_need(
            "resolve-versions",
            missing_need_message="{job_id} must depend on resolve-versions",
        )
    compute_hashes_darwin.require_need(
        "darwin-lock-smoke",
        missing_need_message=(
            "compute-hashes-aarch64-darwin must depend on darwin-lock-smoke"
        ),
    )
    aggregate_platform_updates.require_need(
        "discover-update-targets",
        missing_need_message=(
            "aggregate-platform-updates must depend on discover-update-targets"
        ),
    )
    aggregate_platform_updates.require_need(
        "compute-hashes-aarch64-darwin",
        missing_need_message=(
            "aggregate-platform-updates must depend on compute-hashes-aarch64-darwin"
        ),
    )
    aggregate_platform_updates.require_need(
        "compute-hashes-x86_64-linux",
        missing_need_message=(
            "aggregate-platform-updates must depend on compute-hashes-x86_64-linux"
        ),
    )
    aggregate_platform_updates.require_need(
        "compute-hashes-aarch64-linux",
        missing_need_message=(
            "aggregate-platform-updates must depend on compute-hashes-aarch64-linux"
        ),
    )
    aggregate_if = aggregate_platform_updates.data.get("if")
    if (
        not isinstance(aggregate_if, str)
        or _AGGREGATE_DISCOVER_SUCCESS_MARKER not in aggregate_if
    ):
        msg = (
            "aggregate-platform-updates must skip when target discovery did not "
            "succeed so upstream lock failures do not create secondary aggregate failures"
        )
        raise RuntimeError(msg)
    permissions = create_pr.data.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("actions") != "write":
        msg = "create-pr must grant actions: write to dispatch PR-head validation"
        raise RuntimeError(msg)
    create_pr.require_run_marker(
        _DISPATCH_CI_MARKER,
        missing_run_message="create-pr must dispatch the CI workflow on the update branch",
    )
    create_pr.forbid_run_marker(
        _DISPATCH_CERTIFY_MARKER,
        forbidden_run_message=(
            "create-pr must not dispatch the certification workflow because "
            "update-certify.yml already follows Update via workflow_run"
        ),
    )
    for job in (
        workflow.require_job(job_id="resolve-versions"),
        discover_update_targets,
        compute_hashes_darwin,
        compute_hashes_x86_64_linux,
        compute_hashes_aarch64_linux,
    ):
        _validate_update_api_token(job)
    for job in workflow.jobs.values():
        _validate_checkout_token(job)


def _validate_refresh_workflow_concurrency(workflow_data: WorkflowObject) -> None:
    """Require non-preemptive refresh concurrency for long package matrices."""
    concurrency = workflow_data.get("concurrency")
    cancel_in_progress = (
        concurrency.get("cancel-in-progress") if isinstance(concurrency, dict) else None
    )
    if cancel_in_progress is not False:
        raise RuntimeError(_REFRESH_CONCURRENCY_CANCEL_MESSAGE)


def _validate_checkout_token(job: WorkflowJobAnalysis) -> None:
    """Require the update token for checkout steps in update workflows."""
    for step in job.steps:
        uses = step.get("uses")
        if not isinstance(uses, str) or not uses.startswith(_CHECKOUT_ACTION_PREFIX):
            continue
        config = step.get("with")
        token = config.get("token") if isinstance(config, dict) else None
        if token != _UPDATE_GITHUB_ENV_VALUE:
            msg = (
                f"{job.job_id} checkout steps must use "
                "secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN"
            )
            raise RuntimeError(msg)


def _validate_update_api_token(job: WorkflowJobAnalysis) -> None:
    """Require the high-scope update token for steps that call GitHub APIs."""
    for step in job.steps:
        run = step.get("run")
        if not isinstance(run, str):
            continue
        if not any(marker in run for marker in _UPDATE_API_TOKEN_RUN_MARKERS):
            continue
        env = step.get("env")
        token = env.get("GITHUB_TOKEN") if isinstance(env, dict) else None
        if token != _UPDATE_GITHUB_ENV_VALUE:
            msg = (
                f"{job.job_id} package resolver steps must define "
                "GITHUB_TOKEN to secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN"
            )
            raise RuntimeError(msg)


def _validate_certify_workflow_structure_contracts(workflow: WorkflowAnalysis) -> None:
    (
        select_ref,
        darwin_full_smoke,
        darwin_priority_heavy,
        darwin_extra_heavy,
        darwin_shared,
        darwin_argus,
        darwin_rocinante,
        linux_x86_64,
        publish_pr_certification,
    ) = workflow.require_jobs(
        "select-ref",
        "darwin-full-smoke",
        "darwin-priority-heavy",
        "darwin-extra-heavy",
        "darwin-shared",
        "darwin-argus",
        "darwin-rocinante",
        "linux-x86_64",
        "publish-pr-certification",
    )

    select_ref.require_run_marker(
        _CERTIFICATION_HEAD_SHA_MARKER,
        missing_run_message=(
            "select-ref must read workflow_run head_sha before certifying "
            "the update branch"
        ),
    )
    select_ref.require_run_marker(
        _CERTIFICATION_ANCESTRY_MARKER,
        missing_run_message=(
            "select-ref must skip stale update branches that do not contain "
            "the triggering workflow_run head_sha"
        ),
    )
    for job in workflow.jobs.values():
        _validate_checkout_token(job)

    darwin_full_smoke.require_run_marker(_DARWIN_FULL_SMOKE_MARKER)

    for job in (darwin_priority_heavy, darwin_extra_heavy, darwin_shared):
        job.require_need("darwin-full-smoke")
        job.forbid_need(
            "warm-fod-cache-darwin",
            forbidden_need_message=(
                "{job_id} must not depend on warm-fod-cache-darwin; "
                "FOD warm-up must stay inside the sliced package job"
            ),
        )

    linux_x86_64.forbid_need(
        "warm-fod-cache-x86_64-linux",
        forbidden_need_message=(
            "linux_x86_64 must not depend on warm-fod-cache-x86_64-linux; "
            "FOD warm-up must stay inside the representative Linux job"
        ),
    )

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

    for need in (
        "select-ref",
        "quality-gates",
        "darwin-priority-heavy",
        "darwin-extra-heavy",
        "darwin-shared",
        "darwin-argus",
        "darwin-rocinante",
        "linux-x86_64",
    ):
        publish_pr_certification.require_need(need)

    publish_if = publish_pr_certification.data.get("if")
    if not isinstance(publish_if, str) or not all(
        marker in publish_if
        for marker in (
            "always()",
            "!cancelled()",
            "needs.select-ref.outputs.exists == 'true'",
        )
    ):
        msg = (
            "publish-pr-certification must run after failed certification needs "
            "while preserving select-ref skip and cancel behavior"
        )
        raise RuntimeError(msg)

    publish_pr_certification.require_run_marker(
        _CERTIFICATION_JOBS_API_MARKER,
        missing_run_message=(
            "publish-pr-certification must capture certification job results"
        ),
    )
    publish_pr_certification.require_run_marker(
        _CERTIFICATION_JOBS_JSON_MARKER,
        missing_run_message=(
            "publish-pr-certification must pass certification job results to the renderer"
        ),
    )


def validate_workflow_structure_contracts(*, workflow_path: Path) -> None:
    """Validate refresh/certification structure contracts in one workflow file."""
    workflow_data = load_workflow_yaml(workflow_path)
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
        _validate_refresh_workflow_concurrency(workflow_data)
        _validate_refresh_workflow_structure_contracts(workflow)
    if has_certify_jobs:
        _validate_certify_workflow_structure_contracts(workflow)


__all__ = ["validate_workflow_structure_contracts"]
