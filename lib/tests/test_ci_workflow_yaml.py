"""Tests for shared workflow YAML parsing helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

import lib.update.ci._workflow_yaml as workflow_yaml
from lib.github_actions.models import GitHubWorkflow
from lib.update.paths import REPO_ROOT


def test_workflow_object_stringifies_nested_keys() -> None:
    """Coerce nested YAML mapping keys to strings before JSON coercion."""
    assert workflow_yaml.workflow_object(
        {
            1: [{2: {3: "demo"}}],
            "jobs": {4: {"steps": []}},
        },
        context="workflow demo",
    ) == {
        "1": [{"2": {"3": "demo"}}],
        "jobs": {"4": {"steps": []}},
    }


def test_workflow_job_helpers_cover_jobs_needs_steps_and_runs() -> None:
    """Share job mapping, needs, steps, and run parsing across workflow checks."""
    assert workflow_yaml.workflow_job_map(
        {
            "demo": {
                "steps": [
                    {"run": "echo hi"},
                    "skip-me",
                    {"uses": "actions/checkout@v4"},
                ]
            }
        },
        context="workflow jobs",
    ) == {
        "demo": {
            "steps": [
                {"run": "echo hi"},
                "skip-me",
                {"uses": "actions/checkout@v4"},
            ]
        }
    }
    with pytest.raises(TypeError, match="workflow jobs.demo must be a mapping"):
        workflow_yaml.workflow_job_map({"demo": "nope"}, context="workflow jobs")

    assert workflow_yaml.workflow_job_needs(None, job_id="demo") == ()
    assert workflow_yaml.workflow_job_needs("build", job_id="demo") == ("build",)
    assert workflow_yaml.workflow_job_needs(["build", "test"], job_id="demo") == (
        "build",
        "test",
    )
    with pytest.raises(TypeError, match="unsupported needs value"):
        workflow_yaml.workflow_job_needs(1, job_id="demo")
    with pytest.raises(TypeError, match="non-string need"):
        workflow_yaml.workflow_job_needs(["build", 1], job_id="demo")

    assert workflow_yaml.workflow_job_steps(
        {
            "steps": [
                {"run": "echo hi"},
                "skip-me",
                {"uses": "actions/checkout@v4"},
                {"run": 1},
            ]
        },
        job_id="demo",
    ) == (
        {"run": "echo hi"},
        {"uses": "actions/checkout@v4"},
        {"run": 1},
    )
    assert workflow_yaml.workflow_job_steps({"steps": None}, job_id="demo") == ()
    with pytest.raises(TypeError, match="does not define steps as a list"):
        workflow_yaml.workflow_job_steps({"steps": "nope"}, job_id="demo")

    assert workflow_yaml.workflow_job_run_strings(
        {
            "steps": [
                {"run": "echo hi"},
                {"uses": "actions/checkout@v4"},
                "skip-me",
                {"run": "echo bye"},
                {"run": 1},
            ]
        },
        job_id="demo",
    ) == ("echo hi", "echo bye")
    assert workflow_yaml.workflow_job_run_strings({"steps": None}, job_id="demo") == ()
    with pytest.raises(TypeError, match="does not define steps as a list"):
        workflow_yaml.workflow_job_run_strings({"steps": "nope"}, job_id="demo")


def test_load_workflow_jobs_supports_default_and_custom_messages(
    tmp_path: Path,
) -> None:
    """Load workflow jobs with stable top-level and per-job error messages."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text("name: demo\non: workflow_dispatch\n", encoding="utf-8")

    with pytest.raises(TypeError, match="is missing a top-level jobs mapping"):
        workflow_yaml.load_workflow_jobs(workflow_path)
    with pytest.raises(TypeError, match="does not contain a jobs mapping"):
        workflow_yaml.load_workflow_jobs(
            workflow_path,
            missing_jobs_message="Workflow {workflow_path} does not contain a jobs mapping",
        )

    workflow_path.write_text(
        "name: demo\non: workflow_dispatch\njobs:\n  demo: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(TypeError, match="workflow jobs.demo must be a mapping"):
        workflow_yaml.load_workflow_jobs(workflow_path)
    with pytest.raises(TypeError, match="Workflow job demo must be a mapping"):
        workflow_yaml.load_workflow_jobs(
            workflow_path,
            invalid_job_message="Workflow job {job_id} must be a mapping",
        )

    workflow_path.write_text(
        "name: demo\non: workflow_dispatch\njobs:\n  demo:\n    steps:\n      - run: echo hi\n",
        encoding="utf-8",
    )
    assert workflow_yaml.load_workflow_jobs(workflow_path) == {
        "demo": {"steps": [{"run": "echo hi"}]}
    }


def test_workflow_yaml_helpers_support_typed_and_raw_loading(
    tmp_path: Path,
) -> None:
    """Support both typed model loading and raw normalized workflow mappings."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
name: demo
run-name: Demo run
on:
  workflow_dispatch:
    inputs:
      force:
        description: Force it
        required: true
        type: boolean
jobs:
  demo:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
""",
        encoding="utf-8",
    )

    raw_loaded = workflow_yaml.load_raw_workflow_yaml(workflow_path)
    assert isinstance(raw_loaded, dict)
    assert "on" in raw_loaded
    assert True not in raw_loaded

    loaded_model = workflow_yaml.load_workflow_model(workflow_path)
    assert isinstance(loaded_model, GitHubWorkflow)

    loaded = workflow_yaml.load_workflow_yaml(workflow_path)
    assert loaded == {
        "name": "demo",
        "run-name": "Demo run",
        "on": {
            "workflow_dispatch": {
                "inputs": {
                    "force": {
                        "description": "Force it",
                        "required": True,
                        "type": "boolean",
                    }
                }
            }
        },
        "jobs": {
            "demo": {
                "runs-on": "ubuntu-latest",
                "steps": [{"run": "echo hi"}],
            }
        },
    }


def test_load_workflow_model_rejects_schema_invalid_workflow(tmp_path: Path) -> None:
    """Generated-model loading should still reject schema-invalid workflows."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
name: demo
on: workflow_dispatch
jobs: []
""",
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="failed GitHub Actions schema validation"):
        workflow_yaml.load_workflow_model(workflow_path)


def test_checked_in_update_workflows_validate_against_generated_model() -> None:
    """Schema-compatible checked-in update workflows should support typed loading."""
    for workflow_path in (
        REPO_ROOT / ".github/workflows/update.yml",
        REPO_ROOT / ".github/workflows/update-certify.yml",
    ):
        loaded_model = workflow_yaml.load_workflow_model(workflow_path)
        assert isinstance(loaded_model, GitHubWorkflow)
        assert loaded_model.jobs is not None


def test_repo_workflows_load_as_raw_mappings_even_without_schema_validation() -> None:
    """Checked-in workflows should still load for repo-specific contract checks."""
    workflow = workflow_yaml.load_workflow_yaml(REPO_ROOT / ".github/workflows/ci.yml")

    assert isinstance(workflow, dict)
    assert (
        workflow["jobs"]["quality"]["strategy"]["matrix"]["check"][0] == "format-repo"
    )


def test_workflow_object_rejects_non_mapping_shapes() -> None:
    """Reject non-mapping workflow payloads before downstream validation."""
    with pytest.raises(TypeError, match="Expected JSON object for workflow demo"):
        workflow_yaml.workflow_object([], context="workflow demo")
