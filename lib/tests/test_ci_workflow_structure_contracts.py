"""Tests for higher-level update workflow structure contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.update.ci import workflow_structure_contracts as contracts
from lib.update.ci.workflow_structure_contracts import (
    validate_workflow_structure_contracts,
)
from lib.update.paths import REPO_ROOT


def test_load_jobs_and_require_job_reject_invalid_shapes(tmp_path: Path) -> None:
    """Reject malformed workflow payloads and missing required jobs."""
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("[]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="did not parse to a mapping"):
        contracts._load_jobs(workflow)

    workflow.write_text("name: demo\n", encoding="utf-8")
    with pytest.raises(TypeError, match="missing a top-level jobs mapping"):
        contracts._load_jobs(workflow)

    with pytest.raises(RuntimeError, match="missing required job 'demo'"):
        contracts._require_job({}, job_id="demo")


def test_darwin_shared_heavy_targets_cover_success_and_error_paths() -> None:
    """Validate heavy-target matrix parsing and mismatch detection."""
    with pytest.raises(TypeError, match="strategy mapping"):
        contracts._darwin_shared_heavy_targets({})

    with pytest.raises(TypeError, match="matrix mapping"):
        contracts._darwin_shared_heavy_targets({"strategy": {}})

    with pytest.raises(TypeError, match="non-empty list"):
        contracts._darwin_shared_heavy_targets({
            "strategy": {"matrix": {"include": []}}
        })

    with pytest.raises(TypeError, match="Unsupported darwin-shared-heavy matrix entry"):
        contracts._darwin_shared_heavy_targets({
            "strategy": {"matrix": {"include": [1]}}
        })

    with pytest.raises(TypeError, match="string package/target fields"):
        contracts._darwin_shared_heavy_targets({
            "strategy": {"matrix": {"include": [{"package": "alpha"}]}}
        })

    with pytest.raises(RuntimeError, match="repeats package 'alpha'"):
        contracts._darwin_shared_heavy_targets({
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
        })

    with pytest.raises(RuntimeError, match="package/target mismatch"):
        contracts._darwin_shared_heavy_targets({
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
        })

    assert contracts._darwin_shared_heavy_targets({
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
    }) == (
        ".#pkgs.aarch64-darwin.alpha",
        ".#pkgs.aarch64-darwin.beta",
    )


def test_darwin_shared_exclude_refs_cover_success_and_error_paths() -> None:
    """Validate shared-closure exclude parsing and duplicate detection."""
    with pytest.raises(TypeError, match="steps as a list"):
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


def test_validate_workflow_structure_contracts_detects_missing_only_drift(
    tmp_path: Path,
) -> None:
    """Report heavy targets that are not excluded from the shared closure."""
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        """
        jobs:
          darwin-shared-heavy:
            strategy:
              matrix:
                include:
                  - package: alpha
                    target: .#pkgs.aarch64-darwin.alpha
                  - package: beta
                    target: .#pkgs.aarch64-darwin.beta
            steps: []
          darwin-shared:
            steps:
              - name: Build shared Darwin closure
                run: |
                  nix run .#nixcfg -- ci cache closure \
                    --mode intersection \
                    --exclude-ref .#pkgs.aarch64-darwin.alpha \
                    .#darwinConfigurations.argus.system
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError, match="missing excludes: .#pkgs.aarch64-darwin.beta"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_detects_extra_only_drift(
    tmp_path: Path,
) -> None:
    """Report shared-closure excludes that no longer belong in the heavy split."""
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        """
        jobs:
          darwin-shared-heavy:
            strategy:
              matrix:
                include:
                  - package: alpha
                    target: .#pkgs.aarch64-darwin.alpha
            steps: []
          darwin-shared:
            steps:
              - name: Build shared Darwin closure
                run: |
                  nix run .#nixcfg -- ci cache closure \
                    --mode intersection \
                    --exclude-ref .#pkgs.aarch64-darwin.alpha \
                    --exclude-ref .#pkgs.aarch64-darwin.gamma \
                    .#darwinConfigurations.argus.system
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError, match="unexpected excludes: .#pkgs.aarch64-darwin.gamma"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_update_workflow_structure_contracts_hold() -> None:
    """Keep the checked-in update workflow structurally consistent."""
    validate_workflow_structure_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update.yml"
    )
