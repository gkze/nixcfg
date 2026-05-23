"""Tests for higher-level update workflow structure contracts."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lib.update.ci import workflow_structure_contracts as contracts
from lib.update.ci._workflow_yaml import load_workflow_yaml
from lib.update.ci.workflow_structure_contracts import (
    validate_workflow_structure_contracts,
)
from lib.update.paths import REPO_ROOT


def _write_workflow(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def _valid_refresh_workflow_text() -> str:
    return """
        on: workflow_dispatch
        jobs:
          resolve-versions:
            needs: update-lock
            runs-on: ubuntu-latest
            steps:
              - env:
                  GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
                run: nix run .#nixcfg -- ci pipeline versions --output pinned-versions.json
          darwin-lock-smoke:
            needs: update-lock
            runs-on: ubuntu-latest
            steps:
              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke
          discover-update-targets:
            needs:
              - update-lock
              - resolve-versions
            runs-on: ubuntu-latest
            steps:
              - env:
                  GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
                run: nix run .#nixcfg -- update --list --json > update-targets.json
          compute-hashes-aarch64-darwin:
            needs:
              - update-lock
              - darwin-lock-smoke
              - resolve-versions
              - discover-update-targets
            runs-on: ubuntu-latest
            steps:
              - env:
                  GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
                run: nix run .#nixcfg -- update --native-only "${{ matrix.target }}"
          compute-hashes-x86_64-linux:
            needs:
              - update-lock
              - resolve-versions
              - discover-update-targets
            runs-on: ubuntu-latest
            steps:
              - env:
                  GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
                run: nix run .#nixcfg -- update --native-only "${{ matrix.target }}"
          compute-hashes-aarch64-linux:
            needs:
              - update-lock
              - resolve-versions
              - discover-update-targets
            runs-on: ubuntu-latest
            steps:
              - env:
                  GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
                run: nix run .#nixcfg -- update --native-only "${{ matrix.target }}"
          aggregate-platform-updates:
            needs:
              - discover-update-targets
              - compute-hashes-aarch64-darwin
              - compute-hashes-x86_64-linux
              - compute-hashes-aarch64-linux
            if: >-
              always() && !cancelled() &&
              needs.discover-update-targets.result == 'success'
            runs-on: ubuntu-latest
          create-pr:
            runs-on: ubuntu-latest
            permissions:
              actions: write
              contents: write
              pull-requests: write
            steps:
              - run: |
                  gh workflow run ci.yml --ref "${UPDATE_BRANCH}"
    """


def _valid_certify_workflow_text() -> str:
    return """
        on: workflow_dispatch
        jobs:
          select-ref:
            runs-on: ubuntu-latest
            steps:
              - run: |
                  WORKFLOW_RUN_HEAD_SHA="${WORKFLOW_RUN_HEAD_SHA}"
                  git merge-base --is-ancestor \
                    "${WORKFLOW_RUN_HEAD_SHA}" "refs/remotes/origin/$ref"
          darwin-full-smoke:
            runs-on: ubuntu-latest
            steps:
              - run: nix run .#nixcfg -- ci workflow darwin eval-full-smoke
          darwin-priority-heavy:
            needs:
              - darwin-full-smoke
            runs-on: ubuntu-latest
            strategy:
              matrix:
                include:
                  - package: alpha
                    target: .#pkgs.aarch64-darwin.alpha
          darwin-extra-heavy:
            needs:
              - darwin-full-smoke
            runs-on: ubuntu-latest
            strategy:
              matrix:
                include:
                  - package: beta
                    target: .#pkgs.aarch64-darwin.beta
          darwin-shared:
            needs:
              - darwin-full-smoke
            runs-on: ubuntu-latest
            steps:
              - run: |
                  nix run .#nixcfg -- ci cache closure \
                    --exclude-ref .#pkgs.aarch64-darwin.alpha \
                    --exclude-ref .#pkgs.aarch64-darwin.beta
          darwin-argus:
            needs:
              - darwin-priority-heavy
              - darwin-shared
            runs-on: ubuntu-latest
          darwin-rocinante:
            needs:
              - darwin-priority-heavy
              - darwin-shared
            runs-on: ubuntu-latest
          linux-x86_64:
            needs: []
            runs-on: ubuntu-latest
          publish-pr-certification:
            needs:
              - select-ref
              - quality-gates
              - darwin-priority-heavy
              - darwin-extra-heavy
              - darwin-shared
              - darwin-argus
              - darwin-rocinante
              - linux-x86_64
            if: always() && !cancelled() && needs.select-ref.outputs.exists == 'true'
            runs-on: ubuntu-latest
            steps:
              - run: >-
                  gh api
                  "repos/${{ github.repository }}/actions/runs/${{ github.run_id }}/jobs?per_page=100"
                  > /tmp/certification-jobs.json
              - run: >-
                  nix run .#nixcfg -- ci workflow render-certification-pr-body
                  --jobs-json /tmp/certification-jobs.json
    """


def test_darwin_heavy_targets_cover_success_and_error_paths() -> None:
    """Validate Darwin heavy-target matrix parsing and mismatch detection."""
    with pytest.raises(TypeError, match="strategy mapping"):
        contracts._darwin_heavy_targets({}, job_id="darwin-priority-heavy")

    with pytest.raises(TypeError, match="matrix mapping"):
        contracts._darwin_heavy_targets(
            {"strategy": {}}, job_id="darwin-priority-heavy"
        )

    with pytest.raises(TypeError, match="non-empty list"):
        contracts._darwin_heavy_targets(
            {"strategy": {"matrix": {"include": []}}},
            job_id="darwin-priority-heavy",
        )

    with pytest.raises(
        TypeError, match="Unsupported darwin-priority-heavy matrix entry"
    ):
        contracts._darwin_heavy_targets(
            {"strategy": {"matrix": {"include": [1]}}},
            job_id="darwin-priority-heavy",
        )

    with pytest.raises(TypeError, match="string package/target fields"):
        contracts._darwin_heavy_targets(
            {"strategy": {"matrix": {"include": [{"package": "alpha"}]}}},
            job_id="darwin-priority-heavy",
        )

    with pytest.raises(RuntimeError, match="repeats package 'alpha'"):
        contracts._darwin_heavy_targets(
            {
                "strategy": {
                    "matrix": {
                        "include": [
                            {
                                "package": "alpha",
                                "target": ".#pkgs.aarch64-darwin.alpha",
                            },
                            {
                                "package": "alpha",
                                "target": ".#pkgs.aarch64-darwin.alpha",
                            },
                        ]
                    }
                }
            },
            job_id="darwin-priority-heavy",
        )

    with pytest.raises(RuntimeError, match="package/target mismatch"):
        contracts._darwin_heavy_targets(
            {
                "strategy": {
                    "matrix": {
                        "include": [
                            {
                                "package": "alpha",
                                "target": ".#pkgs.aarch64-darwin.beta",
                            }
                        ]
                    }
                }
            },
            job_id="darwin-priority-heavy",
        )

    assert contracts._darwin_heavy_targets(
        {
            "strategy": {
                "matrix": {
                    "include": [
                        {
                            "package": "alpha",
                            "target": ".#pkgs.aarch64-darwin.alpha",
                        },
                        {
                            "package": "beta",
                            "target": ".#pkgs.aarch64-darwin.beta",
                        },
                    ]
                }
            }
        },
        job_id="darwin-priority-heavy",
    ) == (
        ".#pkgs.aarch64-darwin.alpha",
        ".#pkgs.aarch64-darwin.beta",
    )


def test_darwin_split_targets_reject_duplicate_targets_across_jobs() -> None:
    """Priority and extra jobs must not claim the same heavy target."""
    jobs = {
        "darwin-priority-heavy": {
            "strategy": {
                "matrix": {
                    "include": [
                        {
                            "package": "alpha",
                            "target": ".#pkgs.aarch64-darwin.alpha",
                        }
                    ]
                }
            }
        },
        "darwin-extra-heavy": {
            "strategy": {
                "matrix": {
                    "include": [
                        {
                            "package": "alpha",
                            "target": ".#pkgs.aarch64-darwin.alpha",
                        }
                    ]
                }
            }
        },
    }

    with pytest.raises(
        RuntimeError, match="already declared by 'darwin-priority-heavy'"
    ):
        contracts._darwin_split_targets(
            jobs,
            job_ids=("darwin-priority-heavy", "darwin-extra-heavy"),
        )


def test_darwin_shared_exclude_refs_cover_success_and_error_paths() -> None:
    """Validate shared-closure exclude parsing and duplicate detection."""
    with pytest.raises(
        RuntimeError, match="missing the shared Darwin closure build step"
    ):
        contracts._darwin_shared_exclude_refs({})

    with pytest.raises(
        RuntimeError, match="missing the shared Darwin closure build step"
    ):
        contracts._darwin_shared_exclude_refs({"steps": [{"run": "echo hi"}]})

    with pytest.raises(
        RuntimeError, match="multiple shared Darwin closure build steps"
    ):
        contracts._darwin_shared_exclude_refs({
            "steps": [
                {"run": "nix run .#nixcfg -- ci cache closure --exclude-ref .#a"},
                {"run": "nix run .#nixcfg -- ci cache closure --exclude-ref .#b"},
            ]
        })

    with pytest.raises(RuntimeError, match="does not define any --exclude-ref targets"):
        contracts._darwin_shared_exclude_refs({
            "steps": [
                {"run": "nix run .#nixcfg -- ci cache closure --mode intersection"}
            ]
        })

    with pytest.raises(RuntimeError, match="repeats one or more --exclude-ref targets"):
        contracts._darwin_shared_exclude_refs({
            "steps": [
                {
                    "run": (
                        "nix run .#nixcfg -- ci cache closure "
                        "--exclude-ref .#pkgs.aarch64-darwin.alpha "
                        "--exclude-ref .#pkgs.aarch64-darwin.alpha"
                    )
                }
            ]
        })

    assert contracts._darwin_shared_exclude_refs({
        "steps": [
            {
                "run": (
                    "nix run .#nixcfg -- ci cache closure "
                    "--exclude-ref '.#pkgs.aarch64-darwin.alpha' "
                    '--exclude-ref ".#pkgs.aarch64-darwin.beta"'
                )
            },
            "skip-me",
        ]
    }) == (
        ".#pkgs.aarch64-darwin.alpha",
        ".#pkgs.aarch64-darwin.beta",
    )


def test_validate_workflow_structure_contracts_accepts_valid_refresh_workflow(
    tmp_path: Path,
) -> None:
    """Accept a refresh workflow that honors the early Darwin lock smoke."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml", _valid_refresh_workflow_text()
    )
    validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_accepts_valid_certify_workflow(
    tmp_path: Path,
) -> None:
    """Accept a certification workflow with the heavy Darwin split intact."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml", _valid_certify_workflow_text()
    )
    validate_workflow_structure_contracts(workflow_path=workflow)


@pytest.mark.parametrize(
    ("workflow_text", "error_match"),
    [
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke",
                "echo hi",
                1,
            ),
            "missing required run step",
            id="requires-lock-smoke-marker",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "steps:\n              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke",
                (
                    "steps:\n"
                    "              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke\n"
                    "              - run: nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
                ),
                1,
            ),
            "must not run step",
            id="rejects-full-smoke-in-lock-phase",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "          darwin-lock-smoke:\n            needs: update-lock",
                "          darwin-lock-smoke:\n            needs: resolve-versions",
                1,
            ),
            "must depend on update-lock",
            id="requires-update-lock-dependency",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "          darwin-lock-smoke:\n            needs: update-lock",
                "          darwin-lock-smoke:\n"
                "            needs:\n"
                "              - update-lock\n"
                "              - merge-generated",
                1,
            ),
            "must stay in the lock-only phase",
            id="rejects-merge-generated-in-lock-phase",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "- darwin-lock-smoke", "- resolve-versions", 1
            ),
            "compute-hashes-aarch64-darwin must depend on darwin-lock-smoke",
            id="requires-darwin-compute-hashes-lock-smoke",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "- discover-update-targets", "- update-lock", 1
            ),
            "must depend on discover-update-targets",
            id="requires-compute-hashes-discovery",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "- compute-hashes-aarch64-linux", "- discover-update-targets", 1
            ),
            "aggregate-platform-updates must depend on compute-hashes-aarch64-linux",
            id="requires-aggregate-aarch64-linux",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "            if: >-\n"
                "              always() && !cancelled() &&\n"
                "              needs.discover-update-targets.result == 'success'\n",
                "",
                1,
            ),
            "target discovery did not succeed",
            id="requires-aggregate-discovery-guard",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}",
                "${{ secrets.GITHUB_TOKEN }}",
                1,
            ),
            "must define GITHUB_TOKEN to secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN",
            id="requires-update-api-token",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                "              actions: write\n", "", 1
            ),
            "must grant actions: write",
            id="requires-pr-dispatch-permission",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace("gh workflow run ci.yml", "echo", 1),
            "must dispatch the CI workflow",
            id="requires-ci-dispatch",
        ),
        pytest.param(
            _valid_refresh_workflow_text().replace(
                'gh workflow run ci.yml --ref "${UPDATE_BRANCH}"',
                'gh workflow run ci.yml --ref "${UPDATE_BRANCH}"\n'
                '                  gh workflow run update-certify.yml --ref "${UPDATE_BRANCH}" '
                '-f ref="${UPDATE_BRANCH}"',
                1,
            ),
            "must not dispatch the certification workflow",
            id="forbids-duplicate-certification-dispatch",
        ),
    ],
)
def test_validate_workflow_structure_contracts_rejects_refresh_drift(
    tmp_path: Path,
    workflow_text: str,
    error_match: str,
) -> None:
    """The refresh workflow should reject each critical structural drift."""
    workflow = _write_workflow(tmp_path / "workflow.yml", workflow_text)
    with pytest.raises(RuntimeError, match=error_match):
        validate_workflow_structure_contracts(workflow_path=workflow)


@pytest.mark.parametrize(
    ("workflow_text", "error_match"),
    [
        pytest.param(
            _valid_certify_workflow_text().replace(
                "nix run .#nixcfg -- ci workflow darwin eval-full-smoke",
                "echo hi",
                1,
            ),
            "missing required run step",
            id="requires-full-smoke-marker",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "          darwin-priority-heavy:\n"
                "            needs:\n"
                "              - darwin-full-smoke",
                ("          darwin-priority-heavy:\n            needs: []"),
                1,
            ),
            "darwin-priority-heavy must depend on darwin-full-smoke",
            id="requires-priority-darwin-full-smoke",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "          darwin-extra-heavy:\n"
                "            needs:\n"
                "              - darwin-full-smoke",
                ("          darwin-extra-heavy:\n            needs: []"),
                1,
            ),
            "darwin-extra-heavy must depend on darwin-full-smoke",
            id="requires-extra-darwin-full-smoke",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                (
                    "          darwin-shared:\n"
                    "            needs:\n"
                    "              - darwin-full-smoke"
                ),
                ("          darwin-shared:\n            needs: []"),
                1,
            ),
            "darwin-shared must depend on darwin-full-smoke",
            id="requires-shared-darwin-full-smoke",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "              - darwin-full-smoke",
                "              - darwin-full-smoke\n              - quality-gates",
                1,
            ),
            "darwin-priority-heavy must not depend on quality-gates",
            id="ungates-cache-jobs-from-quality-gates",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "              - darwin-full-smoke",
                "              - darwin-full-smoke\n              - warm-fod-cache-darwin",
                1,
            ),
            "FOD warm-up must stay inside the sliced package job",
            id="ungates-package-jobs-from-global-fod-warmup",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "          linux-x86_64:\n            needs: []",
                (
                    "          linux-x86_64:\n"
                    "            needs:\n"
                    "              - warm-fod-cache-x86_64-linux"
                ),
                1,
            ),
            "FOD warm-up must stay inside the representative Linux job",
            id="ungates-linux-from-global-fod-warmup",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "              - darwin-priority-heavy\n              - darwin-shared",
                (
                    "              - darwin-priority-heavy\n"
                    "              - darwin-extra-heavy\n"
                    "              - darwin-shared"
                ),
                1,
            ),
            "darwin-argus must not depend on darwin-extra-heavy",
            id="hosts-ignore-extra-heavy-gate",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "                  - package: alpha\n"
                "                    target: .#pkgs.aarch64-darwin.alpha\n",
                (
                    "                  - package: alpha\n"
                    "                    target: .#pkgs.aarch64-darwin.alpha\n"
                    "                  - package: gamma\n"
                    "                    target: .#pkgs.aarch64-darwin.gamma\n"
                ),
                1,
            ),
            "missing excludes: .#pkgs.aarch64-darwin.gamma",
            id="detects-missing-only-drift",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "                    --exclude-ref .#pkgs.aarch64-darwin.beta",
                (
                    "                    --exclude-ref .#pkgs.aarch64-darwin.beta\n"
                    "                    --exclude-ref .#pkgs.aarch64-darwin.gamma"
                ),
                1,
            ),
            "unexpected excludes: .#pkgs.aarch64-darwin.gamma",
            id="detects-extra-only-drift",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "WORKFLOW_RUN_HEAD_SHA",
                "TRIGGERING_SHA",
            ),
            "must read workflow_run head_sha",
            id="select-ref-reads-triggering-head-sha",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "git merge-base --is-ancestor",
                "git rev-parse --verify",
                1,
            ),
            "must skip stale update branches",
            id="select-ref-rejects-stale-update-branches",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                (
                    "if: always() && !cancelled() && "
                    "needs.select-ref.outputs.exists == 'true'"
                ),
                "if: needs.select-ref.outputs.exists == 'true'",
                1,
            ),
            "must run after failed certification needs",
            id="publish-runs-after-failed-needs",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "/actions/runs/${{ github.run_id }}/jobs?per_page=100",
                "/actions/runs/${{ github.run_id }}",
                1,
            ),
            "must capture certification job results",
            id="publish-captures-job-results",
        ),
        pytest.param(
            _valid_certify_workflow_text().replace(
                "--jobs-json /tmp/certification-jobs.json",
                "--workflow .github/workflows/update-certify.yml",
                1,
            ),
            "must pass certification job results",
            id="publish-passes-job-results-to-renderer",
        ),
    ],
)
def test_validate_workflow_structure_contracts_rejects_certify_drift(
    tmp_path: Path,
    workflow_text: str,
    error_match: str,
) -> None:
    """The certification workflow should reject each critical structural drift."""
    workflow = _write_workflow(tmp_path / "workflow.yml", workflow_text)
    with pytest.raises(RuntimeError, match=error_match):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_rejects_unknown_workflow_kind(
    tmp_path: Path,
) -> None:
    """Reject workflow files that do not describe refresh or certification jobs."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        """
        on: workflow_dispatch
        jobs:
          demo:
            runs-on: ubuntu-latest
            steps:
              - run: "true"
        """,
    )
    with pytest.raises(RuntimeError, match="does not define refresh or certification"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_update_refresh_workflow_structure_contracts_hold() -> None:
    """Keep the checked-in refresh workflow structurally consistent."""
    validate_workflow_structure_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update.yml"
    )


def test_ci_workflow_can_be_dispatched_for_update_pr_heads() -> None:
    """The update workflow dispatches CI explicitly after bot-created PR updates."""
    payload = load_workflow_yaml(REPO_ROOT / ".github/workflows/ci.yml")
    triggers = payload["on"]
    assert isinstance(triggers, dict)
    assert "workflow_dispatch" in triggers


def test_update_certify_workflow_structure_contracts_hold() -> None:
    """Keep the checked-in certification workflow structurally consistent."""
    validate_workflow_structure_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update-certify.yml"
    )
