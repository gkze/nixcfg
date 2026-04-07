"""Tests for higher-level update workflow structure contracts."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lib.update.ci import workflow_structure_contracts as contracts
from lib.update.ci.workflow_structure_contracts import (
    validate_workflow_structure_contracts,
)
from lib.update.paths import REPO_ROOT


def _write_workflow(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def _valid_refresh_workflow_text() -> str:
    return """
        jobs:
          darwin-lock-smoke:
            needs: update-lock
            steps:
              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke
          compute-hashes:
            needs:
              - update-lock
              - darwin-lock-smoke
              - resolve-versions
            steps: []
    """


def _valid_certify_workflow_text() -> str:
    return """
        jobs:
          darwin-full-smoke:
            steps:
              - run: nix run .#nixcfg -- ci workflow darwin eval-full-smoke
          darwin-priority-heavy:
            needs:
              - darwin-full-smoke
              - warm-fod-cache-darwin
            strategy:
              matrix:
                include:
                  - package: alpha
                    target: .#pkgs.aarch64-darwin.alpha
          darwin-extra-heavy:
            needs:
              - darwin-full-smoke
              - warm-fod-cache-darwin
            strategy:
              matrix:
                include:
                  - package: beta
                    target: .#pkgs.aarch64-darwin.beta
            steps: []
          darwin-shared:
            needs:
              - darwin-full-smoke
              - warm-fod-cache-darwin
            steps:
              - run: |
                  nix run .#nixcfg -- ci cache closure \
                    --exclude-ref .#pkgs.aarch64-darwin.alpha \
                    --exclude-ref .#pkgs.aarch64-darwin.beta
          darwin-argus:
            needs:
              - darwin-priority-heavy
              - darwin-shared
            steps: []
          darwin-rocinante:
            needs:
              - darwin-priority-heavy
              - darwin-shared
            steps: []
          linux-x86_64:
            needs:
              - warm-fod-cache-x86_64-linux
            steps: []
    """


def test_load_jobs_and_require_job_reject_invalid_shapes(tmp_path: Path) -> None:
    """Reject malformed workflow payloads and missing required jobs."""
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("[]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="did not parse to a mapping"):
        contracts._load_jobs(workflow)

    workflow.write_text("name: demo\n", encoding="utf-8")
    with pytest.raises(TypeError, match="missing a top-level jobs mapping"):
        contracts._load_jobs(workflow)

    workflow.write_text("jobs:\n  demo: nope\n", encoding="utf-8")
    with pytest.raises(TypeError, match="Workflow job demo must be a mapping"):
        contracts._load_jobs(workflow)

    with pytest.raises(RuntimeError, match="missing required job 'demo'"):
        contracts._require_job({}, job_id="demo")


def test_parse_job_needs_and_job_run_steps_cover_shapes() -> None:
    """Parse supported needs forms and collect runnable shell steps."""
    assert contracts._parse_job_needs({}, job_id="demo") == ()
    assert contracts._parse_job_needs({"needs": "build"}, job_id="demo") == ("build",)
    assert contracts._parse_job_needs({"needs": ["build", "test"]}, job_id="demo") == (
        "build",
        "test",
    )
    with pytest.raises(TypeError, match="unsupported needs"):
        contracts._parse_job_needs({"needs": ["build", 1]}, job_id="demo")

    with pytest.raises(TypeError, match="does not define steps as a list"):
        contracts._job_run_steps({}, job_id="demo")

    assert contracts._job_run_steps(
        {
            "steps": [
                {"run": "echo hi"},
                {"uses": "actions/checkout@v4"},
                "skip-me",
                {"run": "echo bye"},
            ]
        },
        job_id="demo",
    ) == ("echo hi", "echo bye")


def test_require_and_forbid_job_run_cover_success_and_failure_paths() -> None:
    """Detect required and forbidden run markers clearly."""
    job = {"steps": [{"run": "echo hi"}, {"run": "echo bye"}]}
    contracts._require_job_run(job, job_id="demo", marker="echo hi")
    contracts._forbid_job_run(job, job_id="demo", marker="nix build")

    with pytest.raises(RuntimeError, match="missing required run step"):
        contracts._require_job_run(job, job_id="demo", marker="nix build")
    with pytest.raises(RuntimeError, match="must not run step"):
        contracts._forbid_job_run(job, job_id="demo", marker="echo hi")


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


def test_validate_workflow_structure_contracts_requires_lock_smoke_marker(
    tmp_path: Path,
) -> None:
    """The refresh workflow must keep using the lock-smoke command."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_refresh_workflow_text().replace(
            "nix run .#nixcfg -- ci workflow darwin eval-lock-smoke",
            "echo hi",
            1,
        ),
    )
    with pytest.raises(RuntimeError, match="missing required run step"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_rejects_full_smoke_in_lock_phase(
    tmp_path: Path,
) -> None:
    """The refresh workflow must never run the full-smoke command."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_refresh_workflow_text().replace(
            "steps:\n              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke",
            (
                "steps:\n"
                "              - run: nix run .#nixcfg -- ci workflow darwin eval-lock-smoke\n"
                "              - run: nix run .#nixcfg -- ci workflow darwin eval-full-smoke"
            ),
            1,
        ),
    )
    with pytest.raises(RuntimeError, match="must not run step"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_update_lock_dependency(
    tmp_path: Path,
) -> None:
    """The refresh workflow must stay anchored to update-lock."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_refresh_workflow_text().replace(
            "needs: update-lock", "needs: resolve-versions", 1
        ),
    )
    with pytest.raises(RuntimeError, match="must depend on update-lock"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_rejects_merge_generated_in_lock_phase(
    tmp_path: Path,
) -> None:
    """The early Darwin lock smoke must not slide into generated artifacts."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_refresh_workflow_text().replace(
            "needs: update-lock",
            "needs:\n              - update-lock\n              - merge-generated",
            1,
        ),
    )
    with pytest.raises(RuntimeError, match="must stay in the lock-only phase"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_compute_hashes_lock_smoke(
    tmp_path: Path,
) -> None:
    """Cross-platform hash computation must wait for the early lock smoke."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_refresh_workflow_text().replace(
            "- darwin-lock-smoke", "- resolve-versions", 1
        ),
    )
    with pytest.raises(
        RuntimeError, match="compute-hashes must depend on darwin-lock-smoke"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_full_smoke_marker(
    tmp_path: Path,
) -> None:
    """The certification workflow must keep the full Darwin smoke step."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "nix run .#nixcfg -- ci workflow darwin eval-full-smoke",
            "echo hi",
            1,
        ),
    )
    with pytest.raises(RuntimeError, match="missing required run step"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_priority_darwin_full_smoke(
    tmp_path: Path,
) -> None:
    """Priority Darwin fan-out must wait for the full Darwin smoke."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "          darwin-priority-heavy:\n"
            "            needs:\n"
            "              - darwin-full-smoke\n"
            "              - warm-fod-cache-darwin",
            (
                "          darwin-priority-heavy:\n"
                "            needs:\n"
                "              - warm-fod-cache-darwin"
            ),
            1,
        ),
    )
    with pytest.raises(
        RuntimeError, match="darwin-priority-heavy must depend on darwin-full-smoke"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_extra_darwin_full_smoke(
    tmp_path: Path,
) -> None:
    """Extra Darwin fan-out must wait for the full Darwin smoke."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "          darwin-extra-heavy:\n"
            "            needs:\n"
            "              - darwin-full-smoke\n"
            "              - warm-fod-cache-darwin",
            (
                "          darwin-extra-heavy:\n"
                "            needs:\n"
                "              - warm-fod-cache-darwin"
            ),
            1,
        ),
    )
    with pytest.raises(
        RuntimeError, match="darwin-extra-heavy must depend on darwin-full-smoke"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_requires_shared_darwin_full_smoke(
    tmp_path: Path,
) -> None:
    """Shared Darwin closure computation must wait for the full Darwin smoke."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            (
                "          darwin-shared:\n"
                "            needs:\n"
                "              - darwin-full-smoke\n"
                "              - warm-fod-cache-darwin"
            ),
            (
                "          darwin-shared:\n"
                "            needs:\n"
                "              - warm-fod-cache-darwin"
            ),
            1,
        ),
    )
    with pytest.raises(
        RuntimeError, match="darwin-shared must depend on darwin-full-smoke"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_ungates_cache_jobs_from_quality_gates(
    tmp_path: Path,
) -> None:
    """Cache-producing certify jobs must not depend on quality-gates."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "              - warm-fod-cache-darwin",
            "              - warm-fod-cache-darwin\n              - quality-gates",
            1,
        ),
    )
    with pytest.raises(
        RuntimeError, match="darwin-priority-heavy must not depend on quality-gates"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_hosts_ignore_extra_heavy_gate(
    tmp_path: Path,
) -> None:
    """Host builds should wait on priority-heavy targets, not extra-heavy ones."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "              - darwin-priority-heavy\n              - darwin-shared",
            (
                "              - darwin-priority-heavy\n"
                "              - darwin-extra-heavy\n"
                "              - darwin-shared"
            ),
            1,
        ),
    )
    with pytest.raises(
        RuntimeError, match="darwin-argus must not depend on darwin-extra-heavy"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_detects_missing_only_drift(
    tmp_path: Path,
) -> None:
    """Report heavy targets that are not excluded from the shared closure."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
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
    )

    with pytest.raises(
        RuntimeError, match="missing excludes: .#pkgs.aarch64-darwin.gamma"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_detects_extra_only_drift(
    tmp_path: Path,
) -> None:
    """Report shared-closure excludes that no longer belong in the heavy split."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        _valid_certify_workflow_text().replace(
            "                    --exclude-ref .#pkgs.aarch64-darwin.beta",
            (
                "                    --exclude-ref .#pkgs.aarch64-darwin.beta\n"
                "                    --exclude-ref .#pkgs.aarch64-darwin.gamma"
            ),
            1,
        ),
    )

    with pytest.raises(
        RuntimeError, match="unexpected excludes: .#pkgs.aarch64-darwin.gamma"
    ):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_validate_workflow_structure_contracts_rejects_unknown_workflow_kind(
    tmp_path: Path,
) -> None:
    """Reject workflow files that do not describe refresh or certification jobs."""
    workflow = _write_workflow(
        tmp_path / "workflow.yml",
        """
        jobs:
          demo:
            steps:
              - run: true
        """,
    )
    with pytest.raises(RuntimeError, match="does not define refresh or certification"):
        validate_workflow_structure_contracts(workflow_path=workflow)


def test_update_refresh_workflow_structure_contracts_hold() -> None:
    """Keep the checked-in refresh workflow structurally consistent."""
    validate_workflow_structure_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update.yml"
    )


def test_update_certify_workflow_structure_contracts_hold() -> None:
    """Keep the checked-in certification workflow structurally consistent."""
    validate_workflow_structure_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update-certify.yml"
    )
