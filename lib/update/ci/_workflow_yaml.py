"""Shared GitHub Actions workflow YAML parsing helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

import yaml
from pydantic import ValidationError

from lib import json_utils
from lib.github_actions.models import GitHubWorkflow

type WorkflowValue = json_utils.JsonValue
type WorkflowObject = json_utils.JsonObject

_YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
_YAML_1_2_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class GitHubActionsYamlLoader(yaml.SafeLoader):
    """PyYAML loader adjusted to match GitHub Actions' YAML 1.2 booleans."""

    yaml_implicit_resolvers: ClassVar[dict[object, list[tuple[object, object]]]] = {
        key: list(value)
        for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


for first_char, resolvers in tuple(
    GitHubActionsYamlLoader.yaml_implicit_resolvers.items()
):
    GitHubActionsYamlLoader.yaml_implicit_resolvers[first_char] = [
        (tag, pattern) for tag, pattern in resolvers if tag != _YAML_BOOL_TAG
    ]
GitHubActionsYamlLoader.add_implicit_resolver(
    _YAML_BOOL_TAG,
    _YAML_1_2_BOOL_RE,
    list("tTfF"),
)


def stringify_yaml_keys(value: object) -> object:
    """Recursively coerce YAML mapping keys to strings."""
    if isinstance(value, dict):
        return {str(key): stringify_yaml_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [stringify_yaml_keys(item) for item in value]
    return value


def workflow_object(value: object, *, context: str) -> WorkflowObject:
    """Return ``value`` as a JSON-compatible workflow mapping."""
    return json_utils.coerce_json_object(stringify_yaml_keys(value), context=context)


def workflow_job_map(
    value: object,
    *,
    context: str,
    invalid_job_message: str = "{context}.{job_id} must be a mapping",
) -> dict[str, WorkflowObject]:
    """Return one workflow ``jobs`` mapping as normalized job objects."""
    jobs = workflow_object(value, context=context)
    workflow_jobs: dict[str, WorkflowObject] = {}
    for job_id, job_data in jobs.items():
        if not isinstance(job_data, dict):
            msg = invalid_job_message.format(context=context, job_id=job_id)
            raise TypeError(msg)
        workflow_jobs[job_id] = workflow_object(
            job_data,
            context=f"{context}.{job_id}",
        )
    return workflow_jobs


def workflow_job_needs(
    raw_needs: object,
    *,
    job_id: str,
    invalid_value_message: str = (
        "Job {job_id} defines unsupported needs value: {raw_needs!r}"
    ),
    invalid_item_message: str = "Job {job_id} contains non-string need: {need!r}",
) -> tuple[str, ...]:
    """Return normalized job dependencies from one workflow job."""
    if raw_needs is None:
        return ()
    if isinstance(raw_needs, str):
        return (raw_needs,)
    if not isinstance(raw_needs, list):
        msg = invalid_value_message.format(job_id=job_id, raw_needs=raw_needs)
        raise TypeError(msg)

    parsed: list[str] = []
    for need in raw_needs:
        if not isinstance(need, str):
            msg = invalid_item_message.format(
                job_id=job_id,
                need=need,
                raw_needs=raw_needs,
            )
            raise TypeError(msg)
        parsed.append(need)
    return tuple(parsed)


def workflow_job_steps(
    job_data: WorkflowObject,
    *,
    job_id: str,
    invalid_steps_message: str = "Job {job_id} does not define steps as a list",
) -> tuple[WorkflowObject, ...]:
    """Return one job's dict-shaped steps as normalized workflow objects."""
    raw_steps = job_data.get("steps", [])
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list):
        msg = invalid_steps_message.format(job_id=job_id)
        raise TypeError(msg)

    return tuple(
        workflow_object(step, context=f"workflow job {job_id} step {step_index}")
        for step_index, step in enumerate(raw_steps, start=1)
        if isinstance(step, dict)
    )


def workflow_job_run_strings(
    job_data: WorkflowObject,
    *,
    job_id: str,
    invalid_steps_message: str = "Job {job_id} does not define steps as a list",
) -> tuple[str, ...]:
    """Return one job's string-valued ``run`` steps in declared order."""
    return tuple(
        run
        for step in workflow_job_steps(
            job_data,
            job_id=job_id,
            invalid_steps_message=invalid_steps_message,
        )
        if isinstance(run := step.get("run"), str)
    )


def load_raw_workflow_yaml(workflow_path: Path) -> object:
    """Load one workflow file without applying model validation."""
    return yaml.load(
        workflow_path.read_text(encoding="utf-8"),
        Loader=GitHubActionsYamlLoader,  # noqa: S506
    )


def workflow_model(value: object, *, context: str) -> GitHubWorkflow:
    """Validate a workflow payload with the generated GitHub Actions model.

    This is intentionally optional/best-effort. The generated model comes from
    SchemaStore's workflow schema, which is useful for typed access but is not
    authoritative enough to reject every real GitHub workflow we may want to
    inspect in-repo.
    """
    payload = workflow_object(value, context=context)
    try:
        return GitHubWorkflow.model_validate(payload)
    except ValidationError as exc:
        msg = f"{context} failed GitHub Actions schema validation: {exc}"
        raise TypeError(msg) from exc


def load_workflow_model(workflow_path: Path) -> GitHubWorkflow:
    """Load and validate one workflow file into the generated workflow model."""
    return workflow_model(
        load_raw_workflow_yaml(workflow_path),
        context=f"workflow {workflow_path}",
    )


def load_workflow_yaml(workflow_path: Path) -> WorkflowObject:
    """Load one workflow file into a normalized raw mapping.

    We deliberately do not hard-require generated-model validation here. The
    generated SchemaStore-backed model is available via ``load_workflow_model``
    for typed access, but some valid GitHub workflow constructs used in this
    repo are broader than that schema currently accepts.
    """
    context = f"workflow {workflow_path}"
    return workflow_object(load_raw_workflow_yaml(workflow_path), context=context)


def load_workflow_jobs(
    workflow_path: Path,
    *,
    context: str = "workflow jobs",
    missing_jobs_message: str = (
        "Workflow {workflow_path} is missing a top-level jobs mapping"
    ),
    invalid_job_message: str = "{context}.{job_id} must be a mapping",
) -> dict[str, WorkflowObject]:
    """Load one workflow file and normalize its top-level ``jobs`` mapping."""
    payload = load_workflow_yaml(workflow_path)
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        msg = missing_jobs_message.format(workflow_path=workflow_path)
        raise TypeError(msg)

    return workflow_job_map(
        jobs,
        context=context,
        invalid_job_message=invalid_job_message,
    )


__all__ = [
    "GitHubActionsYamlLoader",
    "WorkflowObject",
    "WorkflowValue",
    "load_raw_workflow_yaml",
    "load_workflow_jobs",
    "load_workflow_model",
    "load_workflow_yaml",
    "stringify_yaml_keys",
    "workflow_job_map",
    "workflow_job_needs",
    "workflow_job_run_strings",
    "workflow_job_steps",
    "workflow_model",
    "workflow_object",
]
