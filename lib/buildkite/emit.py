"""Render a Buildkite pipeline from the Actions job graph.

Static matrices become one step per cell. A matrix whose axes come from a
job output becomes a fan-out step; that step uploads the cells and every job
that was waiting on them. ``depends_on`` follows ``needs``.
"""

import json
import re
from typing import TYPE_CHECKING

from lib.buildkite.github_expr import (
    ExprContext,
    expression_text,
    parse,
    references_needs,
    truthy,
)
from lib.buildkite.graph import Job, Matrix, Workflow, WorkflowParseError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_STEP_COMMAND = "nix develop --command python -m lib.buildkite.step"
_FANOUT_QUEUE = "linux-x86"


def step_key(workflow: str, job_id: str, cell: Mapping[str, str] | None = None) -> str:
    """Return the Buildkite step key for one Actions job or matrix cell."""
    stem = workflow.removesuffix(".yml")
    base = f"{stem}--{job_id}"
    if not cell:
        return base
    return f"{base}--{cell_token(cell)}"


def cell_token(cell: Mapping[str, str]) -> str:
    """Return a step-key suffix for one matrix cell."""
    if "shard" in cell:
        return "shard-" + _slug(cell["shard"])
    return "--".join(f"{key}-{_slug(value)}" for key, value in sorted(cell.items()))


def actions_job_name(job_id: str, cell: Mapping[str, str] | None) -> str:
    """Return the job name ``sibling_failfast`` matches, including a matrix suffix."""
    if not cell:
        return job_id
    if len(cell) == 1:
        return f"{job_id} ({next(iter(cell.values()))})"
    shard = cell.get("shard")
    if shard is not None:
        return f"{job_id} ({shard})"
    rendered = ", ".join(cell[key] for key in sorted(cell))
    return f"{job_id} ({rendered})"


def static_cells(matrix: Matrix | None) -> tuple[dict[str, str], ...]:
    """Expand a static matrix.

    A dynamic matrix and a job with no matrix yield an empty cell list.
    """
    if matrix is None:
        return ()
    if matrix.dynamic:
        return ()
    cells: list[dict[str, str]] = [{}]
    for key, values in matrix.axes:
        cells = [{**cell, key: value} for cell in cells for value in values]
    return tuple(cells)


def cells_from_output(payload: object) -> tuple[dict[str, str], ...]:
    """Expand a ``fromJSON`` matrix output.

    Axes are a cartesian product. ``include`` rows are cells, which is the
    shape ``plan-shards`` writes for Darwin closure shards.
    """
    if not isinstance(payload, dict):
        message = "matrix output is not an object"
        raise WorkflowParseError(message)
    included = payload.get("include")
    if isinstance(included, list):
        cells: list[dict[str, str]] = []
        for row in included:
            if not isinstance(row, dict):
                message = "matrix include row is not an object"
                raise WorkflowParseError(message)
            cells.append({str(key): str(value) for key, value in row.items()})
        return tuple(cells)
    axes = [
        (str(key), value) for key, value in payload.items() if isinstance(value, list)
    ]
    cells = [{}]
    for key, values in axes:
        cells = [{**cell, key: str(value)} for cell in cells for value in values]
    return tuple(cells)


def _downstream(workflow: Workflow, job_id: str) -> set[str]:
    """Return *job_id* and every job that waits on it, directly or not."""
    downstream = {job_id}
    changed = True
    while changed:
        changed = False
        for job in workflow.jobs:
            if job.job_id in downstream:
                continue
            if any(need in downstream for need in job.needs):
                downstream.add(job.job_id)
                changed = True
    return downstream


def deferred_job_ids(workflow: Workflow) -> frozenset[str]:
    """Return jobs that wait, directly or not, on a runtime matrix."""
    deferred: set[str] = set()
    changed = True
    while changed:
        changed = False
        for job in workflow.jobs:
            if job.job_id in deferred:
                continue
            waits = job.matrix is not None and job.matrix.dynamic
            if waits or any(need in deferred for need in job.needs):
                deferred.add(job.job_id)
                changed = True
    return frozenset(deferred)


def render_pipeline(
    workflows: Sequence[Workflow],
    *,
    context: ExprContext,
) -> dict[str, object]:
    """Return the pipeline uploaded at the start of a build."""
    groups = [
        group
        for workflow in workflows
        if (group := _group(workflow, context)) is not None
    ]
    if not groups:
        return {
            "steps": [
                {
                    "label": "No GitHub Actions workflow matches this build",
                    "key": "no-matching-workflow",
                    "command": "true",
                }
            ]
        }
    return {"steps": groups}


def render_continuation(
    workflow: Workflow,
    job_id: str,
    cells: Sequence[Mapping[str, str]],
    *,
    context: ExprContext,
) -> dict[str, object]:
    """Return the steps a fan-out uploads after *job_id*'s matrix exists.

    Downstream jobs that are still blocked on a later dynamic matrix are not
    included. A nested fan-out step is included when that later matrix's other
    needs are already running.
    """
    expanding = workflow.job(job_id)
    if expanding.matrix is None or not expanding.matrix.dynamic:
        message = f"{job_id} is not a dynamic matrix"
        raise WorkflowParseError(message)
    pending = _downstream(workflow, job_id)
    deferred = set(pending)
    satisfied = {job.job_id for job in workflow.jobs if job.job_id not in pending}
    satisfied.add(job_id)
    steps: list[dict[str, object]] = [
        _concrete_step(workflow, expanding, dict(cell), context) for cell in cells
    ]
    if cells:
        steps.append(_barrier(workflow, expanding, cells))
    else:
        steps.append(_skipped_marker(workflow, expanding))
    _append_unblocked(workflow, context, satisfied, deferred, steps, skip={job_id})
    return {"steps": steps}


def allow_dependency_failure(if_expr: str | None) -> bool:
    """Return whether a step must run after a failed ``needs`` job.

    Actions ``if: always()`` still starts the job. Buildkite skips a step
    whose dependency failed unless this flag is set. The command then applies
    the original condition.
    """
    if if_expr is None:
        return False
    text = " ".join(if_expr.split())
    return "always()" in text or "'failure'" in text or "'skipped'" in text


def statically_untrusted(if_expr: str | None, context: ExprContext) -> bool:
    """Return whether *if_expr* is already false without reading ``needs``.

    A step that cannot run does not receive secrets. Fork builds therefore do
    not see the trusted Cachix token on the write-proof job.
    """
    if if_expr is None:
        return False
    try:
        parsed = parse(if_expr)
    except ValueError:
        return False
    if references_needs(parsed):
        return False
    try:
        return not truthy(context.eval(parsed))
    except ValueError:
        return False


def _group(workflow: Workflow, context: ExprContext) -> dict[str, object] | None:
    deferred = deferred_job_ids(workflow)
    steps: list[dict[str, object]] = []
    for job in workflow.jobs:
        if job.job_id in deferred:
            continue
        if job.matrix is not None and job.matrix.axes:
            steps.extend(
                _concrete_step(workflow, job, cell, context)
                for cell in static_cells(job.matrix)
            )
            continue
        steps.extend(_approval_and_step(workflow, job, None, context))
    for job in workflow.jobs:
        if job.matrix is None or not job.matrix.dynamic:
            continue
        if any(need in deferred for need in job.needs):
            continue
        steps.append(_fanout_step(workflow, job))
    if not steps:
        return None
    return {
        "group": workflow.name,
        "key": workflow.filename.removesuffix(".yml"),
        "steps": steps,
    }


def _append_unblocked(
    workflow: Workflow,
    context: ExprContext,
    satisfied: set[str],
    deferred: set[str],
    steps: list[dict[str, object]],
    *,
    skip: set[str],
) -> None:
    changed = True
    while changed:
        changed = False
        for job in workflow.jobs:
            if job.job_id in satisfied or job.job_id in skip:
                continue
            if any(need not in satisfied for need in job.needs):
                continue
            if job.matrix is not None and job.matrix.dynamic:
                steps.append(_fanout_step(workflow, job))
                skip.add(job.job_id)
                changed = True
                continue
            if job.matrix is not None and job.matrix.axes:
                steps.extend(
                    _concrete_step(workflow, job, cell, context)
                    for cell in static_cells(job.matrix)
                )
            else:
                steps.extend(_approval_and_step(workflow, job, None, context))
            satisfied.add(job.job_id)
            deferred.discard(job.job_id)
            changed = True


def _approval_and_step(
    workflow: Workflow,
    job: Job,
    cell: Mapping[str, str] | None,
    context: ExprContext,
) -> list[dict[str, object]]:
    step = _concrete_step(workflow, job, cell, context)
    if job.environment is None:
        return [step]
    approval = step_key(workflow.filename, job.job_id) + "--approval"
    block: dict[str, object] = {
        "block": f"Approve {job.environment}",
        "key": approval,
        "depends_on": step["depends_on"],
        "if": 'build.env("NIXCFG_BUILDKITE_PUBLISH") == "true"',
    }
    depends = step["depends_on"]
    if not isinstance(depends, list):
        message = "depends_on must be a list"
        raise WorkflowParseError(message)
    step["depends_on"] = [*depends, approval]
    return [block, step]


def _concrete_step(
    workflow: Workflow,
    job: Job,
    cell: Mapping[str, str] | None,
    context: ExprContext,
) -> dict[str, object]:
    if job.queue is None:
        message = f"{job.job_id} has no runner queue"
        raise WorkflowParseError(message)
    key = step_key(workflow.filename, job.job_id, cell or None)
    command = f"{_STEP_COMMAND} run {workflow.filename} {job.job_id}"
    step: dict[str, object] = {
        "label": actions_job_name(job.job_id, cell or None),
        "key": key,
        "command": command,
        "agents": {"queue": job.queue},
        "depends_on": _dependency_keys(workflow, job),
    }
    if cell:
        step["env"] = {"NIXCFG_BUILDKITE_CELL": json.dumps(cell, sort_keys=True)}
    if job.timeout_minutes is not None:
        step["timeout_in_minutes"] = job.timeout_minutes
    if allow_dependency_failure(job.if_expr):
        step["allow_dependency_failure"] = True
    secrets = () if statically_untrusted(job.if_expr, context) else job.secrets
    if secrets:
        step["secrets"] = list(secrets)
    if (
        job.matrix is not None
        and job.matrix.max_parallel is not None
        and cell is not None
    ):
        stem = workflow.filename.removesuffix(".yml")
        step["concurrency"] = job.matrix.max_parallel
        step["concurrency_group"] = f"matrix-{stem}-{job.job_id}-$BUILDKITE_BUILD_ID"
    return step


def _fanout_step(workflow: Workflow, job: Job) -> dict[str, object]:
    key = step_key(workflow.filename, job.job_id) + "--fanout"
    return {
        "label": f"{job.job_id} matrix",
        "key": key,
        "command": f"{_STEP_COMMAND} fanout {workflow.filename} {job.job_id}",
        "agents": {"queue": _FANOUT_QUEUE},
        "depends_on": _dependency_keys(workflow, job),
        "timeout_in_minutes": 10,
        "allow_dependency_failure": allow_dependency_failure(job.if_expr),
    }


def _barrier(
    workflow: Workflow, job: Job, cells: Sequence[Mapping[str, str]]
) -> dict[str, object]:
    """Record one matrix result after every cell has finished, including failures."""
    return {
        "label": f"{job.job_id} result",
        "key": step_key(workflow.filename, job.job_id),
        "command": f"{_STEP_COMMAND} aggregate {workflow.filename} {job.job_id}",
        "agents": {"queue": _FANOUT_QUEUE},
        "depends_on": [
            step_key(workflow.filename, job.job_id, dict(cell)) for cell in cells
        ],
        "allow_dependency_failure": True,
        "timeout_in_minutes": 10,
    }


def _skipped_marker(workflow: Workflow, job: Job) -> dict[str, object]:
    """Keep a key for a matrix that produced no cells."""
    return {
        "label": actions_job_name(job.job_id, None),
        "key": step_key(workflow.filename, job.job_id),
        "command": f"{_STEP_COMMAND} skip {workflow.filename} {job.job_id}",
        "agents": {"queue": job.queue or _FANOUT_QUEUE},
        "depends_on": [],
    }


def _dependency_keys(workflow: Workflow, job: Job) -> list[str]:
    keys: list[str] = []
    for need in job.needs:
        needed = workflow.job(need)
        if needed.matrix is not None and needed.matrix.dynamic:
            keys.append(step_key(workflow.filename, need))
            continue
        cells = static_cells(needed.matrix)
        if cells:
            keys.extend(step_key(workflow.filename, need, cell) for cell in cells)
            continue
        keys.append(step_key(workflow.filename, need))
    return keys


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", value)
    return cleaned or "cell"


def concurrency_expression(text: str) -> str:
    """Return an expression body, whether or not it is wrapped in ``${{ }}``."""
    return expression_text(text) or text
