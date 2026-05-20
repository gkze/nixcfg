"""Structure tests for update self-healing workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from lib.update.ci import self_heal
from lib.update.ci._workflow_yaml import (
    GitHubActionsYamlLoader,
    load_workflow_yaml,
)
from lib.update.paths import REPO_ROOT

AGENTIC_SOURCE = REPO_ROOT / ".github/workflows/update-self-heal.md"
AGENTIC_LOCK = REPO_ROOT / ".github/workflows/update-self-heal.lock.yml"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
REPAIR_COMPANION = REPO_ROOT / ".github/workflows/update-self-heal-pr.yml"


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    _, raw, _ = text.split("---", 2)
    loaded = yaml.load(raw, Loader=GitHubActionsYamlLoader)  # noqa: S506
    assert isinstance(loaded, dict)
    return loaded


def _run_texts(workflow: dict[str, Any]) -> tuple[str, ...]:
    runs: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", ()):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                runs.append(step["run"])
    return tuple(runs)


def _ci_required_check_names() -> tuple[str, ...]:
    workflow = load_workflow_yaml(CI_WORKFLOW)
    return (
        "commitlint",
        *workflow["jobs"]["quality"]["strategy"]["matrix"]["check"],
        "lint-pins-pinact",
        "verify-crate2nix",
    )


def _checkout_refs(job: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for step in job.get("steps", ()):
        if not isinstance(step, dict):
            continue
        if (
            step.get("uses")
            != "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd"
        ):
            continue
        with_args = step.get("with", {})
        if isinstance(with_args, dict) and isinstance(with_args.get("ref"), str):
            refs.append(with_args["ref"])
    return tuple(refs)


def _markdown_fenced_blocks(path: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    text = path.read_text(encoding="utf-8")
    blocks: list[tuple[str, tuple[str, ...]]] = []
    current_lang: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("```"):
            if current_lang is None:
                current_lang = line.removeprefix("```").strip()
                current_lines = []
            else:
                blocks.append((current_lang, tuple(current_lines)))
                current_lang = None
                current_lines = []
            continue
        if current_lang is not None:
            current_lines.append(line)

    assert current_lang is None
    return tuple(blocks)


def _markdown_inline_code_spans(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(re.findall(r"`([^`\n]+)`", text))


def test_agentic_source_pins_copilot_model_and_safe_outputs() -> None:
    """The source workflow uses Copilot and deterministic safe outputs."""
    workflow = _frontmatter(AGENTIC_SOURCE)

    assert workflow["engine"] == {"id": "copilot", "model": "gpt-4.1"}
    assert workflow["safe-outputs"]["github-token"] == (
        "${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}"
    )
    assert workflow["safe-outputs"]["create-pull-request"] == {
        "title-prefix": "[agentic update repair] ",
        "labels": ["agentic-update-repair"],
        "draft": False,
        "max": 1,
        "base-branch": "main",
        "allowed-base-branches": ["main"],
        "preserve-branch-name": True,
        "fallback-as-issue": False,
        "auto-close-issue": False,
        "protected-files": "allowed",
        "allowed-files": [
            "packages/**",
            "overlays/**",
            "lib/tests/**",
            "tests/**",
            "docs/**",
            "misc/**",
        ],
        "github-token-for-extra-empty-commit": (
            "${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}"
        ),
    }
    assert set(workflow["safe-outputs"]["jobs"]) == {
        "retry-failed-jobs",
        "record-stop",
    }
    pre_agent_runs = "\n".join(step["run"] for step in workflow["steps"])
    assert "remaining-cycles" in pre_agent_runs
    pre_agent_env = "\n".join(
        str(value)
        for step in workflow["steps"]
        for value in step.get("env", {}).values()
    )
    assert "secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN" in pre_agent_env
    assert workflow["on"]["workflow_run"]["workflows"] == [
        "Update",
        "Update: Certify",
        "CI",
    ]
    assert workflow["on"]["workflow_run"]["branches"] == [
        "main",
        "agentic/update-self-heal/**",
    ]
    assert "github.event.workflow_run.name == 'CI'" in workflow["if"]
    assert (
        "startsWith(github.event.workflow_run.head_branch, 'agentic/update-self-heal/')"
        in workflow["if"]
    )
    assert _checkout_refs(workflow["safe-outputs"]["jobs"]["retry-failed-jobs"]) == (
        "main",
    )
    assert _checkout_refs(workflow["safe-outputs"]["jobs"]["record-stop"]) == ("main",)


def test_expected_repair_checks_track_ci_workflow() -> None:
    """The branch-protection allowlist matches the PR CI surface."""
    assert _ci_required_check_names() == self_heal.EXPECTED_REPAIR_PR_REQUIRED_CHECKS


def test_agentic_source_requires_parsed_auto_fix_classifier_marker() -> None:
    """Repair PR instructions require a validated classifier marker."""
    sh_blocks = {
        lines for lang, lines in _markdown_fenced_blocks(AGENTIC_SOURCE) if lang == "sh"
    }

    assert (
        "python3 -m lib.update.ci.self_heal parse-classifier classifier.json",
    ) in sh_blocks
    assert (
        "python3 -m lib.update.ci.self_heal render-classifier-marker \\",
        "  --require-auto-fix classifier.json",
    ) in sh_blocks
    assert (
        f"<!-- {self_heal.CLASSIFIER_MARKER_NAME}:{{...}} -->"
        in _markdown_inline_code_spans(AGENTIC_SOURCE)
    )


def test_compiled_agentic_lock_tracks_source_and_model() -> None:
    """The checked-in lock file is compiled from the source workflow."""
    first_line = AGENTIC_LOCK.read_text(encoding="utf-8").splitlines()[0]
    metadata = json.loads(first_line.removeprefix("# gh-aw-metadata: "))
    workflow = load_workflow_yaml(AGENTIC_LOCK)

    assert metadata["agent_id"] == "copilot"
    assert metadata["agent_model"] == "gpt-4.1"
    assert metadata["strict"] is True
    assert workflow["on"]["workflow_run"]["workflows"] == [
        "Update",
        "Update: Certify",
        "CI",
    ]
    assert workflow["on"]["workflow_run"]["branches"] == [
        "main",
        "agentic/update-self-heal/**",
    ]
    assert "workflow_dispatch" in workflow["on"]
    assert (
        "github.event.workflow_run.name == 'CI'"
        in workflow["jobs"]["pre_activation"]["if"]
    )
    assert "agentic/update-self-heal/" in workflow["jobs"]["pre_activation"]["if"]


def test_repair_companion_enables_auto_merge_only_after_gates() -> None:
    """Repair PRs are auto-merged only after deterministic branch-protection checks."""
    workflow = load_workflow_yaml(REPAIR_COMPANION)
    runs = "\n".join(_run_texts(workflow))
    enable_job = workflow["jobs"]["enable-auto-merge"]

    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert workflow["on"]["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "closed",
    ]
    assert _checkout_refs(workflow["jobs"]["classify-pr"]) == (
        "${{ github.event.pull_request.base.sha }}",
    )
    assert _checkout_refs(enable_job) == ("${{ github.event.pull_request.base.sha }}",)
    assert "github.event.action != 'closed'" in enable_job["if"]
    assert "verify-auto-fix-classifier" in runs
    assert "gh pr diff" in runs
    assert "validate-auto-fix-paths" in runs
    assert "remaining-cycles" in runs
    assert "--attempt-kind repair" in runs
    assert "required-checks-present" in runs
    assert "protection/required_status_checks" in runs
    assert "gh pr merge" in runs
    assert "--auto" in runs
    assert "--squash" in runs
    assert "--delete-branch" in runs


def test_repair_companion_dispatches_update_after_merge() -> None:
    """Merged repair PRs dispatch update.yml from main."""
    workflow = load_workflow_yaml(REPAIR_COMPANION)
    dispatch_job = workflow["jobs"]["dispatch-update-after-merge"]
    runs = "\n".join(_run_texts({"jobs": {"dispatch": dispatch_job}}))

    assert dispatch_job["if"] == (
        "needs.classify-pr.outputs.should-dispatch-update == 'true'"
    )
    assert "actions: write" in yaml.safe_dump(dispatch_job["permissions"])
    assert "gh workflow run update.yml --ref main" in runs
