"""Tests for the shared workflow analysis layer."""

from __future__ import annotations

from pathlib import Path

import pytest

import lib.update.ci._workflow_analysis as workflow_analysis


def test_workflow_job_analysis_shares_cached_needs_runs_and_matrix_helpers() -> None:
    """Analyze one job once and reuse parsed needs, runs, and matrix entries."""
    job = workflow_analysis.analyze_workflow_job(
        "demo",
        {
            "needs": ["build", "test"],
            "steps": [
                {"run": "echo hi"},
                "skip-me",
                {"uses": "actions/checkout@v4"},
                {"run": "echo bye"},
            ],
            "strategy": {
                "matrix": {
                    "include": [
                        {"package": "alpha", "target": ".#alpha"},
                        {"package": "beta", "target": ".#beta"},
                    ]
                }
            },
        },
    )

    assert job.needs == ("build", "test")
    assert job.has_need("build") is True
    assert job.has_need("deploy") is False
    job.require_need("build")
    job.forbid_need("deploy")
    assert job.steps == (
        {"run": "echo hi"},
        {"uses": "actions/checkout@v4"},
        {"run": "echo bye"},
    )
    assert job.run_strings == ("echo hi", "echo bye")
    assert job.has_run_marker("echo hi") is True
    assert job.has_run_marker("missing") is False
    job.require_run_marker("echo hi")
    job.forbid_run_marker("missing")
    assert job.optional_matrix_include() == (
        {"package": "alpha", "target": ".#alpha"},
        {"package": "beta", "target": ".#beta"},
    )

    with pytest.raises(RuntimeError, match="demo must depend on deploy"):
        job.require_need("deploy")
    with pytest.raises(RuntimeError, match="demo must not depend on build"):
        job.forbid_need("build")
    with pytest.raises(RuntimeError, match="missing required run step"):
        job.require_run_marker("missing")
    with pytest.raises(RuntimeError, match="must not run step"):
        job.forbid_run_marker("echo hi")


@pytest.mark.parametrize(
    ("job_data", "message"),
    [
        ({}, "demo does not define a strategy mapping"),
        ({"strategy": {}}, "demo does not define a matrix mapping"),
        (
            {"strategy": {"matrix": {"include": []}}},
            "demo matrix.include must be a non-empty list",
        ),
        (
            {"strategy": {"matrix": {"include": [1]}}},
            "Unsupported demo matrix entry: 1",
        ),
    ],
)
def test_workflow_job_analysis_preserves_required_matrix_error_contracts(
    job_data: dict[str, object],
    message: str,
) -> None:
    """Strict matrix analysis should keep validator-facing error contracts."""
    job = workflow_analysis.analyze_workflow_job("demo", job_data)

    with pytest.raises(TypeError, match=message):
        job.require_matrix_include()


def test_analyze_workflow_jobs_returns_shared_job_analysis_map() -> None:
    """Analyze raw jobs mappings without loading a workflow file from disk."""
    jobs = workflow_analysis.analyze_workflow_jobs(
        {
            "build": {
                "steps": [{"run": "echo build"}],
            },
            "test": {
                "needs": ["build"],
                "steps": [{"run": "echo test"}],
            },
        },
        context="workflow jobs",
    )

    assert tuple(jobs) == ("build", "test")
    assert jobs["build"].needs == ()
    assert jobs["test"].needs == ("build",)
    assert jobs["test"].run_strings == ("echo test",)


def test_workflow_analysis_from_jobs_coerces_raw_and_analyzed_jobs() -> None:
    """Support direct construction from mixed raw/analyzed workflow job mappings."""
    build = workflow_analysis.analyze_workflow_job(
        "build",
        {"steps": [{"run": "echo build"}]},
    )
    analysis = workflow_analysis.WorkflowAnalysis.from_jobs({
        "build": build,
        "test": {
            "needs": ["build"],
            "steps": [{"run": "echo test"}],
        },
    })

    assert analysis.require_job(job_id="build") is build
    assert analysis.require_job(job_id="test").needs == ("build",)
    assert analysis.require_jobs("build", "test") == (
        build,
        analysis.require_job(job_id="test"),
    )
    assert analysis.has_any_job(("missing", "test")) is True
    assert analysis.has_any_job(("missing", "deploy")) is False


def test_load_workflow_analysis_supports_job_lookup_and_needs_graph(
    tmp_path: Path,
) -> None:
    """Load analyzed jobs from disk and compute direct/transitive needs."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
name: demo
on: workflow_dispatch
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo build
  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - run: echo test
  deploy:
    needs:
      - test
    runs-on: ubuntu-latest
    steps:
      - run: echo deploy
""".lstrip(),
        encoding="utf-8",
    )

    analysis = workflow_analysis.load_workflow_analysis(workflow_path)

    assert analysis.require_job(job_id="test").needs == ("build",)
    assert analysis.require_job(job_id="deploy").run_strings == ("echo deploy",)
    direct_needs, transitive_needs = analysis.needs_graph()
    assert direct_needs == {
        "build": frozenset(),
        "test": frozenset({"build"}),
        "deploy": frozenset({"test"}),
    }
    assert transitive_needs == {
        "build": frozenset(),
        "test": frozenset({"build"}),
        "deploy": frozenset({"build", "test"}),
    }

    with pytest.raises(
        RuntimeError, match="Workflow is missing required job 'missing'"
    ):
        analysis.require_job(job_id="missing")
