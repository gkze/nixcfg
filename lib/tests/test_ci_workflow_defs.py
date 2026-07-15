"""Regression tests for the generated GitHub Actions workflow definitions."""

from __future__ import annotations

import pytest
import yaml

from lib.update.ci import workflow_defs
from lib.update.ci._workflow_yaml import GitHubActionsYamlLoader
from lib.update.ci.update_target_artifacts import REFRESH_FINAL_ARTIFACT_ALLOWED_SPECS
from lib.update.paths import get_repo_root


def _parse(text: str) -> dict[str, object]:
    parsed = yaml.load(text, Loader=GitHubActionsYamlLoader)  # noqa: S506
    assert isinstance(parsed, dict)
    return parsed


def test_generated_workflows_match_committed_files() -> None:
    """The committed workflow files are exactly the rendered typed model.

    This is the drift check exercised from pytest: any hand edit to the
    generated YAML or un-regenerated defs change fails here.
    """
    repo_root = get_repo_root()
    for relative_path, text in workflow_defs.render_generated_workflows().items():
        assert (repo_root / relative_path).read_text(encoding="utf-8") == text, (
            f"{relative_path} drifted; run `nixcfg ci workflow generate`"
        )


def test_rendering_is_deterministic() -> None:
    """Rendering the workflow inventory twice yields identical documents."""
    assert (
        workflow_defs.render_generated_workflows()
        == workflow_defs.render_generated_workflows()
    )


def test_generated_documents_carry_generation_header() -> None:
    """Every generated document is marked as machine-maintained."""
    for text in workflow_defs.render_generated_workflows().values():
        assert text.startswith("--- # !yamlfmt!:ignore\n")
        assert "do not hand-edit" in text.splitlines()[1]


def test_update_workflow_shape() -> None:
    """Keep the phase-structured update pipeline and its trigger state."""
    data = _parse(
        workflow_defs.render_generated_workflows()[".github/workflows/update.yml"]
    )
    assert data["on"] == {"workflow_call": None}
    concurrency = data["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["cancel-in-progress"] is True

    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    assert list(jobs) == [
        "update-lock",
        "darwin-lock-smoke",
        "resolve-versions",
        "discover-update-targets",
        "compute-hashes-aarch64-darwin",
        "compute-hashes-x86_64-linux",
        "compute-hashes-aarch64-linux",
        "aggregate-platform-updates",
        "merge-hashes",
        "crate2nix-linux",
        "crate2nix-darwin",
        "merge-generated",
        "validate-derivations-darwin",
        "refresh-sanity",
        "create-pr",
    ]

    darwin_compute = jobs["compute-hashes-aarch64-darwin"]
    assert isinstance(darwin_compute, dict)
    assert darwin_compute["needs"] == [
        "update-lock",
        "darwin-lock-smoke",
        "resolve-versions",
        "discover-update-targets",
    ]

    create_pr = jobs["create-pr"]
    assert isinstance(create_pr, dict)
    assert create_pr["needs"] == [
        "merge-generated",
        "validate-derivations-darwin",
        "refresh-sanity",
    ]
    assert create_pr["permissions"] == {
        "actions": "write",
        "contents": "write",
        "pull-requests": "write",
    }


def test_update_workflow_final_artifact_matches_python_specs() -> None:
    """The PR artifact uploads exactly the shared generated-output specs."""
    data = _parse(
        workflow_defs.render_generated_workflows()[".github/workflows/update.yml"]
    )
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    refresh_sanity = jobs["refresh-sanity"]
    assert isinstance(refresh_sanity, dict)
    steps = refresh_sanity["steps"]
    assert isinstance(steps, list)
    derivation_validation = next(
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Evaluate update-target package derivations"
    )
    assert derivation_validation["run"] == (
        "nix run .#nixcfg -- ci workflow validate-update-derivations"
    )

    darwin_validation = jobs["validate-derivations-darwin"]
    assert isinstance(darwin_validation, dict)
    assert darwin_validation["needs"] == "merge-generated"
    assert darwin_validation["runs-on"] == "macos-15"
    darwin_steps = darwin_validation["steps"]
    assert isinstance(darwin_steps, list)
    assert any(
        isinstance(step, dict)
        and isinstance(step.get("with"), dict)
        and step["with"].get("name") == "merged-generated"
        for step in darwin_steps
    )
    assert any(
        isinstance(step, dict)
        and step.get("run")
        == "nix run .#nixcfg -- ci workflow validate-update-derivations"
        for step in darwin_steps
    )
    final_upload = next(
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("with"), dict)
        and step["with"].get("name") == "merged-generated-formatted"
    )
    with_data = final_upload["with"]
    assert isinstance(with_data, dict)
    path = with_data["path"]
    assert isinstance(path, str)
    assert tuple(path.splitlines()) == REFRESH_FINAL_ARTIFACT_ALLOWED_SPECS


def test_ci_quality_matrix_covers_fast_and_deep_checks() -> None:
    """The CI quality matrix is the concatenated shared check inventory."""
    data = _parse(
        workflow_defs.render_generated_workflows()[".github/workflows/ci.yml"]
    )
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    quality = jobs["quality"]
    assert isinstance(quality, dict)
    strategy = quality["strategy"]
    assert isinstance(strategy, dict)
    matrix = strategy["matrix"]
    assert isinstance(matrix, dict)
    assert matrix["check"] == [
        *workflow_defs.FAST_QUALITY_CHECKS,
        *workflow_defs.DEEP_QUALITY_CHECKS,
    ]
    assert "verify-workflow-generated" in workflow_defs.DEEP_QUALITY_CHECKS


def test_certify_shared_closure_excludes_every_heavy_target() -> None:
    """The shared-closure step excludes each sliced heavy target exactly once."""
    heavy_entries = (
        *workflow_defs.DARWIN_PRIORITY_HEAVY_ENTRIES,
        *workflow_defs.DARWIN_EXTRA_HEAVY_ENTRIES,
    )
    packages = [entry["package"] for entry in heavy_entries]
    assert len(set(packages)) == len(packages)
    for entry in heavy_entries:
        assert entry["target"] == f".#pkgs.aarch64-darwin.{entry['package']}"

    data = _parse(
        workflow_defs.render_generated_workflows()[
            ".github/workflows/update-certify.yml"
        ]
    )
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    darwin_shared = jobs["darwin-shared"]
    assert isinstance(darwin_shared, dict)
    steps = darwin_shared["steps"]
    assert isinstance(steps, list)
    closure_step = next(
        step
        for step in steps
        if isinstance(step, dict) and step.get("name") == "Build shared Darwin closure"
    )
    run = closure_step["run"]
    assert isinstance(run, str)
    excluded = [
        line.strip().removeprefix("--exclude-ref ").removesuffix(" \\")
        for line in run.splitlines()
        if line.strip().startswith("--exclude-ref ")
    ]
    assert excluded == [entry["target"] for entry in heavy_entries]


@pytest.mark.parametrize(
    "relative_path",
    [
        workflow_defs.UPDATE_WORKFLOW_PATH,
        workflow_defs.CI_WORKFLOW_PATH,
        workflow_defs.UPDATE_CERTIFY_WORKFLOW_PATH,
    ],
)
def test_generated_documents_respect_yamllint_line_budget(
    relative_path: str,
) -> None:
    """Keep rendered lines within the yamllint budget or its allowances."""
    text = workflow_defs.render_generated_workflows()[relative_path]
    lines = text.splitlines()
    disabled = False
    for line in lines:
        if "yamllint disable rule:line-length" in line:
            disabled = True
        if "yamllint enable rule:line-length" in line:
            disabled = False
        if disabled or len(line) <= 100:
            continue
        # yamllint allow-non-breakable-words: overflow must be one word.
        assert " " not in line.strip(), f"overlong breakable line: {line!r}"
