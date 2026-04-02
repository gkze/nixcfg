"""Validate higher-level update workflow structure contracts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

import yaml

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
    raw_steps = job_data.get("steps")
    if not isinstance(raw_steps, list):
        msg = "darwin-shared does not define steps as a list"
        raise TypeError(msg)

    closure_runs: list[str] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if isinstance(run, str) and _SHARED_CLOSURE_MARKER in run:
            closure_runs.append(run)
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
    heavy_targets = set(
        _darwin_shared_heavy_targets(_require_job(jobs, job_id="darwin-shared-heavy"))
    )
    excluded_targets = set(
        _darwin_shared_exclude_refs(_require_job(jobs, job_id="darwin-shared"))
    )

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
