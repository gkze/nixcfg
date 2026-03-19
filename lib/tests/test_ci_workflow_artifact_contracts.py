"""Tests for static workflow artifact contract validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lib.tests._assertions import check
from lib.update.ci import workflow_artifact_contracts as contracts
from lib.update.ci.workflow_artifact_contracts import (
    validate_workflow_artifact_contracts,
)
from lib.update.paths import REPO_ROOT


def _write_file(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_artifact_contract_helper_error_paths(tmp_path: Path) -> None:
    """Cover parser and helper branches used by workflow validation."""
    check(contracts._artifact_root_for(()) == "")
    check(contracts._parse_path_specs(None) == ())
    check(contracts._parse_path_specs(["foo", "", " bar "]) == ("foo", "bar"))
    check(
        contracts._substitute_matrix(["${{ matrix.os }}"], {"os": "linux"}) == ["linux"]
    )
    check(
        contracts._substitute_matrix({"name": "${{ matrix.os }}"}, {"os": "linux"})
        == {"name": "linux"}
    )
    check(contracts._substitute_matrix(1, {"os": "linux"}) == 1)

    try:
        contracts._parse_path_specs("!foo")
    except RuntimeError as exc:
        check("Exclude paths" in str(exc))
    else:
        msg = "expected exclude-path failure"
        raise AssertionError(msg)

    try:
        contracts._parse_path_specs("/tmp/foo")
    except RuntimeError as exc:
        check("Absolute artifact paths" in str(exc))
    else:
        msg = "expected absolute-path failure"
        raise AssertionError(msg)

    check(contracts._parse_job_needs(None, job_id="demo") == ())
    check(contracts._parse_job_needs("build", job_id="demo") == ("build",))

    try:
        contracts._parse_job_needs(1, job_id="demo")
    except TypeError as exc:
        check("unsupported needs value" in str(exc))
    else:
        msg = "expected invalid-needs failure"
        raise AssertionError(msg)

    try:
        contracts._parse_job_needs(["build", 1], job_id="demo")
    except TypeError as exc:
        check("non-string need" in str(exc))
    else:
        msg = "expected non-string need failure"
        raise AssertionError(msg)

    _write_file(tmp_path / "packages/demo/sources.json")
    _write_file(tmp_path / "overlays/demo/sources.json")
    check(
        contracts._resolve_spec_paths(tmp_path, "packages")
        == ("packages/demo/sources.json",)
    )
    check(
        set(
            contracts._materialized_paths_from_run_step(
                {"run": "nix run .#nixcfg -- ci pipeline sources"},
                repo_root=tmp_path,
            )
        )
        == {"overlays/demo/sources.json", "packages/demo/sources.json"}
    )
    check(
        contracts._materialized_paths_from_run_step({"run": 1}, repo_root=tmp_path)
        == ()
    )


def test_expand_jobs_reject_invalid_shapes() -> None:
    """Reject malformed workflow job shapes with clear errors."""
    try:
        contracts._expand_jobs({"bad": {"steps": "nope"}})
    except TypeError as exc:
        check("does not define steps as a list" in str(exc))
    else:
        msg = "expected invalid-steps failure"
        raise AssertionError(msg)

    try:
        contracts._expand_jobs({
            "bad": {
                "steps": ["skip-me"],
                "strategy": {"matrix": {"include": [1]}},
            }
        })
    except TypeError as exc:
        check("Unsupported matrix include entry" in str(exc))
    else:
        msg = "expected invalid-matrix failure"
        raise AssertionError(msg)

    check(contracts._expand_jobs({"ok": {"steps": ["skip-me"]}})[0].steps == ())


def test_expand_jobs_reject_invalid_matrix_substitutions() -> None:
    """Reject matrix expansions that stop producing dict-shaped steps."""
    original = contracts._substitute_matrix

    def _bad_substitute(value: object, matrix_values: dict[str, object]) -> object:
        return [] if isinstance(value, dict) else original(value, matrix_values)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(contracts, "_substitute_matrix", _bad_substitute)
    try:
        try:
            contracts._expand_jobs({
                "bad": {
                    "steps": [{"run": "${{ matrix.os }}"}, "skip-me"],
                    "strategy": {"matrix": {"include": [{"os": "linux"}]}},
                }
            })
        except TypeError as exc:
            check("expanded to []" in str(exc))
        else:
            msg = "expected invalid-substitution failure"
            raise AssertionError(msg)
    finally:
        monkeypatch.undo()

    expanded = contracts._expand_jobs({
        "ok": {
            "steps": [{"run": "${{ matrix.os }}"}, "skip-me"],
            "strategy": {"matrix": {"include": [{"os": "linux"}]}},
        }
    })
    check(expanded[0].steps == ({"run": "linux"},))


def test_build_upload_and_download_reject_invalid_shapes(tmp_path: Path) -> None:
    """Reject malformed upload/download steps with clear errors."""
    try:
        contracts._build_upload(
            {},
            job_id="job",
            job_instance_id="job",
            repo_root=tmp_path,
            step_index=1,
        )
    except TypeError as exc:
        check("missing 'with'" in str(exc))
    else:
        msg = "expected upload-with failure"
        raise AssertionError(msg)

    try:
        contracts._build_upload(
            {"with": {}},
            job_id="job",
            job_instance_id="job",
            repo_root=tmp_path,
            step_index=1,
        )
    except RuntimeError as exc:
        check("missing a name" in str(exc))
    else:
        msg = "expected upload-name failure"
        raise AssertionError(msg)

    dummy_upload = contracts.ArtifactUpload(
        artifact_name="demo",
        artifact_root="",
        job_id="producer",
        job_instance_id="producer",
        source_paths=("foo.txt",),
        step_name="upload",
        stored_paths=("foo.txt",),
    )
    try:
        contracts._build_download({}, job_id="job", step_index=1, upload=dummy_upload)
    except TypeError as exc:
        check("missing 'with'" in str(exc))
    else:
        msg = "expected download-with failure"
        raise AssertionError(msg)


def test_collect_uploads_and_validate_job_flows_cover_error_branches(
    tmp_path: Path,
) -> None:
    """Exercise duplicate uploads and malformed download handling."""
    _write_file(tmp_path / "foo.txt")
    duplicate_jobs = (
        contracts.WorkflowJob(
            job_id="one",
            instance_id="one",
            steps=(
                {
                    "uses": "actions/upload-artifact@v6",
                    "with": {"name": "dup", "path": "foo.txt"},
                },
            ),
        ),
        contracts.WorkflowJob(
            job_id="two",
            instance_id="two",
            steps=(
                {
                    "uses": "actions/upload-artifact@v6",
                    "with": {"name": "dup", "path": "foo.txt"},
                },
            ),
        ),
    )
    _uploads, duplicate_errors = contracts._collect_uploads(
        duplicate_jobs, repo_root=tmp_path
    )
    check(any("uploaded multiple times" in error for error in duplicate_errors))

    malformed_job = contracts.WorkflowJob(
        job_id="consumer",
        instance_id="consumer",
        steps=(
            {"uses": "actions/download-artifact@v7"},
            {"uses": "actions/download-artifact@v7", "with": {}},
            {
                "uses": "actions/download-artifact@v7",
                "with": {"name": "missing", "path": "."},
            },
        ),
    )
    malformed_errors = contracts._validate_job_artifact_flows(
        malformed_job,
        repo_root=tmp_path,
        transitive_needs={"consumer": frozenset()},
        uploads={},
    )
    check(any("missing 'with'" in error for error in malformed_errors))
    check(any("missing a name" in error for error in malformed_errors))
    check(
        any(
            "downloads unknown artifact `missing`" in error
            for error in malformed_errors
        )
    )

    download_job = contracts.WorkflowJob(
        job_id="consumer",
        instance_id="consumer",
        steps=(
            {
                "uses": "actions/download-artifact@v7",
                "with": {"name": "foo", "path": "."},
            },
            {
                "uses": "actions/download-artifact@v7",
                "with": {"name": "foo", "path": "nested"},
            },
        ),
    )
    merged_errors = contracts._validate_job_artifact_flows(
        download_job,
        repo_root=tmp_path,
        transitive_needs={"consumer": frozenset({"producer"})},
        uploads={
            "foo": contracts.ArtifactUpload(
                artifact_name="foo",
                artifact_root="",
                job_id="producer",
                job_instance_id="producer",
                source_paths=("foo.txt",),
                step_name="upload",
                stored_paths=("foo.txt",),
            )
        },
    )
    check(merged_errors == [])

    need_message = contracts._render_missing_need_error(
        artifact_name="foo",
        consumer_job=download_job,
        producer_upload=contracts.ArtifactUpload(
            artifact_name="foo",
            artifact_root="",
            job_id="producer",
            job_instance_id="producer[os=linux]",
            source_paths=("foo.txt",),
            step_name="upload",
            stored_paths=("foo.txt",),
        ),
        transitive_needs=frozenset(),
    )
    check("Transitive needs: (none)" in need_message)
    check(
        "`producer`"
        in contracts._render_missing_need_error(
            artifact_name="foo",
            consumer_job=download_job,
            producer_upload=contracts.ArtifactUpload(
                artifact_name="foo",
                artifact_root="",
                job_id="producer",
                job_instance_id="producer",
                source_paths=("foo.txt",),
                step_name="upload",
                stored_paths=("foo.txt",),
            ),
            transitive_needs=frozenset({"producer"}),
        )
    )


def test_validate_workflow_artifact_contracts_rejects_invalid_top_level_shapes(
    tmp_path: Path,
) -> None:
    """Reject workflows that omit the jobs mapping entirely."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        textwrap.dedent("""
        name: demo
        jobs: []
        """),
        encoding="utf-8",
    )

    try:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path, repo_root=tmp_path
        )
    except TypeError as exc:
        check("does not contain a jobs mapping" in str(exc))
    else:
        msg = "expected invalid-jobs failure"
        raise AssertionError(msg)


def test_build_needs_graph_rejects_unknown_needs() -> None:
    """Reject jobs that depend on undeclared producers."""
    try:
        contracts._build_needs_graph({
            "consumer": {"needs": ["missing"], "steps": []},
            "producer": {"steps": []},
        })
    except RuntimeError as exc:
        check("references unknown needs" in str(exc))
        check("`missing`" in str(exc))
    else:
        msg = "expected unknown-needs failure"
        raise AssertionError(msg)


def test_validate_workflow_artifact_contracts_detects_path_re_rooting(
    tmp_path: Path,
) -> None:
    """Reject downloads that silently strip a shared parent directory."""
    _write_file(tmp_path / "packages/zed-editor-nightly/Cargo.nix")
    _write_file(tmp_path / "packages/zed-editor-nightly/crate-hashes.json")
    _write_file(tmp_path / "packages/opencode-desktop/Cargo.nix")
    _write_file(tmp_path / "packages/opencode-desktop/crate-hashes.json")

    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
jobs:
  crate2nix-darwin:
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: crate2nix-darwin
          path: |
            packages/zed-editor-nightly/Cargo.nix
            packages/zed-editor-nightly/crate-hashes.json
            packages/opencode-desktop/Cargo.nix
            packages/opencode-desktop/crate-hashes.json
  merge-generated:
    needs: crate2nix-darwin
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: crate2nix-darwin
          path: .
      - uses: actions/upload-artifact@v6
        with:
          name: merged-generated
          path: |
            packages/zed-editor-nightly/Cargo.nix
            packages/zed-editor-nightly/crate-hashes.json
            packages/opencode-desktop/Cargo.nix
            packages/opencode-desktop/crate-hashes.json
""".lstrip(),
        encoding="utf-8",
    )

    try:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        msg = "expected validation failure"
        raise AssertionError(msg)

    check("crate2nix-darwin" in message)
    check("packages/zed-editor-nightly/Cargo.nix" in message)
    check("zed-editor-nightly/Cargo.nix" in message)


def test_validate_workflow_artifact_contracts_accepts_explicit_re_rooting(
    tmp_path: Path,
) -> None:
    """Allow consumers that download under the producer artifact root."""
    _write_file(tmp_path / "packages/zed-editor-nightly/Cargo.nix")
    _write_file(tmp_path / "packages/zed-editor-nightly/crate-hashes.json")
    _write_file(tmp_path / "packages/opencode-desktop/Cargo.nix")
    _write_file(tmp_path / "packages/opencode-desktop/crate-hashes.json")

    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
jobs:
  crate2nix-darwin:
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: crate2nix-darwin
          path: |
            packages/zed-editor-nightly/Cargo.nix
            packages/zed-editor-nightly/crate-hashes.json
            packages/opencode-desktop/Cargo.nix
            packages/opencode-desktop/crate-hashes.json
  merge-generated:
    needs: crate2nix-darwin
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: crate2nix-darwin
          path: packages
      - uses: actions/upload-artifact@v6
        with:
          name: merged-generated
          path: |
            packages/zed-editor-nightly/Cargo.nix
            packages/zed-editor-nightly/crate-hashes.json
            packages/opencode-desktop/Cargo.nix
            packages/opencode-desktop/crate-hashes.json
""".lstrip(),
        encoding="utf-8",
    )

    validate_workflow_artifact_contracts(
        workflow_path=workflow_path, repo_root=tmp_path
    )


def test_validate_workflow_artifact_contracts_detects_missing_job_needs(
    tmp_path: Path,
) -> None:
    """Reject artifact downloads that do not depend on the producer job."""
    _write_file(tmp_path / "foo.txt")

    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
jobs:
  producer:
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: foo
          path: foo.txt
  consumer:
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: foo
          path: .
""".lstrip(),
        encoding="utf-8",
    )

    try:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        msg = "expected validation failure"
        raise AssertionError(msg)

    check("does not depend on `producer`" in message)
    check("downloads artifact `foo`" in message)


def test_validate_workflow_artifact_contracts_allows_transitive_needs(
    tmp_path: Path,
) -> None:
    """Allow consumers that reach producers through transitive job needs."""
    _write_file(tmp_path / "foo.txt")

    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
jobs:
  producer:
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: foo
          path: foo.txt
  middle:
    needs: producer
    steps:
      - run: true
  consumer:
    needs: middle
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: foo
          path: .
""".lstrip(),
        encoding="utf-8",
    )

    validate_workflow_artifact_contracts(
        workflow_path=workflow_path, repo_root=tmp_path
    )


def test_validate_workflow_artifact_contracts_rejects_cyclic_needs(
    tmp_path: Path,
) -> None:
    """Reject workflows whose job dependency graph contains a cycle."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
jobs:
  alpha:
    needs: beta
    steps:
      - run: true
  beta:
    needs: alpha
    steps:
      - run: true
""".lstrip(),
        encoding="utf-8",
    )

    try:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        msg = "expected validation failure"
        raise AssertionError(msg)

    check("contain a cycle" in message)
    check("alpha" in message)
    check("beta" in message)


def test_update_workflow_artifact_contracts_hold() -> None:
    """Keep the checked-in update workflow artifact-safe."""
    validate_workflow_artifact_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update.yml",
        repo_root=REPO_ROOT,
    )
