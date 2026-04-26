"""Tests for static workflow artifact contract validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

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
    assert contracts._artifact_root_for(()) == ""
    assert contracts._parse_path_specs(None) == ()
    assert contracts._parse_path_specs(["foo", "", " bar "]) == ("foo", "bar")
    assert contracts._substitute_matrix(["${{ matrix.os }}"], {"os": "linux"}) == [
        "linux"
    ]
    assert contracts._substitute_matrix(
        {"name": "${{ matrix.os }}"}, {"os": "linux"}
    ) == {"name": "linux"}
    assert contracts._substitute_matrix(1, {"os": "linux"}) == 1

    with pytest.raises(RuntimeError, match="Exclude paths"):
        contracts._parse_path_specs("!foo")

    with pytest.raises(RuntimeError, match="Absolute artifact paths"):
        contracts._parse_path_specs("/tmp/foo")

    _write_file(tmp_path / "packages/demo/sources.json")
    _write_file(tmp_path / "overlays/demo/sources.json")
    assert contracts._resolve_spec_paths(tmp_path, "packages") == (
        "packages/demo/sources.json",
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        contracts._resolve_spec_paths(tmp_path, "missing.txt")
    assert set(
        contracts._materialized_paths_from_run_step(
            {"run": "nix run .#nixcfg -- ci pipeline sources"},
            repo_root=tmp_path,
        )
    ) == {"overlays/demo/sources.json", "packages/demo/sources.json"}
    assert contracts._materialized_paths_from_run_step(
        {
            "run": "nix run .#nixcfg -- ci pipeline versions --output pinned-versions.json"
        },
        repo_root=tmp_path,
    ) == ("pinned-versions.json",)
    assert (
        contracts._materialized_paths_from_run_step({"run": 1}, repo_root=tmp_path)
        == ()
    )


def test_expand_jobs_reject_invalid_shapes() -> None:
    """Reject malformed workflow job shapes with clear errors."""
    with pytest.raises(TypeError, match="does not define steps as a list"):
        contracts._expand_jobs({"bad": {"steps": "nope"}})

    with pytest.raises(TypeError, match="Unsupported matrix include entry"):
        contracts._expand_jobs({
            "bad": {
                "steps": ["skip-me"],
                "strategy": {"matrix": {"include": [1]}},
            }
        })

    assert contracts._expand_jobs({"ok": {"steps": ["skip-me"]}})[0].steps == ()
    assert contracts._expand_jobs({"none": {"steps": None}})[0].steps == ()
    assert contracts._expand_jobs({"reusable": {"uses": "./demo.yml"}})[0].steps == ()


def test_expand_jobs_reject_invalid_matrix_substitutions() -> None:
    """Reject matrix expansions that stop producing dict-shaped steps."""
    original = contracts._substitute_matrix

    def _bad_substitute(value: object, matrix_values: dict[str, object]) -> object:
        return [] if isinstance(value, dict) else original(value, matrix_values)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(contracts, "_substitute_matrix", _bad_substitute)
    try:
        with pytest.raises(TypeError, match=r"expanded to \[\]"):
            contracts._expand_jobs({
                "bad": {
                    "steps": [{"run": "${{ matrix.os }}"}, "skip-me"],
                    "strategy": {"matrix": {"include": [{"os": "linux"}]}},
                }
            })
    finally:
        monkeypatch.undo()

    expanded = contracts._expand_jobs({
        "ok": {
            "steps": [{"run": "${{ matrix.os }}"}, "skip-me"],
            "strategy": {"matrix": {"include": [{"os": "linux"}]}},
        }
    })
    assert expanded[0].steps == ({"run": "linux"},)


def test_build_upload_and_download_reject_invalid_shapes(tmp_path: Path) -> None:
    """Reject malformed upload/download steps with clear errors."""
    with pytest.raises(TypeError, match="missing 'with'"):
        contracts._build_upload(
            {},
            job_id="job",
            job_instance_id="job",
            repo_root=tmp_path,
            step_index=1,
        )

    with pytest.raises(RuntimeError, match="missing a name"):
        contracts._build_upload(
            {"with": {}},
            job_id="job",
            job_instance_id="job",
            repo_root=tmp_path,
            step_index=1,
        )

    dummy_upload = contracts.ArtifactUpload(
        artifact_name="demo",
        artifact_root="",
        job_id="producer",
        job_instance_id="producer",
        source_paths=("foo.txt",),
        step_name="upload",
        stored_paths=("foo.txt",),
    )
    with pytest.raises(TypeError, match="missing 'with'"):
        contracts._build_download({}, job_id="job", step_index=1, upload=dummy_upload)


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
    assert any("uploaded multiple times" in error for error in duplicate_errors)

    download_probe_job = contracts.WorkflowJob(
        job_id="download-probe",
        instance_id="download-probe",
        steps=(
            {"uses": "actions/download-artifact@v7"},
            {"uses": "actions/download-artifact@v7", "with": {"name": "missing"}},
            {
                "uses": "actions/upload-artifact@v6",
                "with": {"name": "after-download", "path": "foo.txt"},
            },
        ),
    )
    download_uploads, download_errors = contracts._collect_uploads(
        (download_probe_job,), repo_root=tmp_path
    )
    assert download_errors == []
    assert "after-download" in download_uploads

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
    assert any("missing 'with'" in error for error in malformed_errors)
    assert any("missing a name" in error for error in malformed_errors)
    assert any(
        "downloads unknown artifact `missing`" in error for error in malformed_errors
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
    assert merged_errors == []

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
    assert "Transitive needs: (none)" in need_message
    assert "`producer`" in contracts._render_missing_need_error(
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


def test_validate_workflow_artifact_contracts_rejects_invalid_top_level_shapes(
    tmp_path: Path,
) -> None:
    """Reject workflows that omit the jobs mapping entirely."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        textwrap.dedent("""
        name: demo
        on: workflow_dispatch
        jobs: []
        """),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="does not contain a jobs mapping"):
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path, repo_root=tmp_path
        )


def test_validate_workflow_artifact_contracts_defaults_workflow_path_from_repo_root(
    tmp_path: Path,
) -> None:
    """Default workflow lookup should stay rooted at the caller-provided repo path."""
    workflow_path = tmp_path / ".github/workflows/update.yml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        textwrap.dedent("""
        name: demo
        on: workflow_dispatch
        jobs: []
        """),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="does not contain a jobs mapping"):
        validate_workflow_artifact_contracts(repo_root=tmp_path)


def test_build_needs_graph_rejects_unknown_needs() -> None:
    """Reject jobs that depend on undeclared producers."""
    with pytest.raises(RuntimeError, match=r"references unknown needs.*`missing`"):
        contracts._build_needs_graph({
            "consumer": {"needs": ["missing"], "steps": []},
            "producer": {"steps": []},
        })


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
on: workflow_dispatch
jobs:
  crate2nix-darwin:
    runs-on: ubuntu-latest
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
    runs-on: ubuntu-latest
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

    with pytest.raises(RuntimeError) as exc_info:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    message = str(exc_info.value)

    assert "crate2nix-darwin" in message
    assert "packages/zed-editor-nightly/Cargo.nix" in message
    assert "zed-editor-nightly/Cargo.nix" in message


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
on: workflow_dispatch
jobs:
  crate2nix-darwin:
    runs-on: ubuntu-latest
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
    runs-on: ubuntu-latest
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
on: workflow_dispatch
jobs:
  producer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: foo
          path: foo.txt
  consumer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: foo
          path: .
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    message = str(exc_info.value)

    assert "does not depend on `producer`" in message
    assert "downloads artifact `foo`" in message


def test_validate_workflow_artifact_contracts_rejects_unmaterialized_upload_paths(
    tmp_path: Path,
) -> None:
    """Reject uploads whose files are neither present nor produced earlier in the job."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
on: workflow_dispatch
jobs:
  producer:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
      - uses: actions/upload-artifact@v6
        with:
          name: generated
          path: pinned-versions.json
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="pinned-versions.json"):
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )


def test_validate_workflow_artifact_contracts_allows_known_generated_upload_paths(
    tmp_path: Path,
) -> None:
    """Allow uploads after known workflow helpers materialize their outputs."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
on: workflow_dispatch
jobs:
  producer:
    runs-on: ubuntu-latest
    steps:
      - run: nix run .#nixcfg -- ci pipeline versions --output pinned-versions.json
      - uses: actions/upload-artifact@v6
        with:
          name: generated
          path: pinned-versions.json
""".lstrip(),
        encoding="utf-8",
    )

    validate_workflow_artifact_contracts(
        workflow_path=workflow_path,
        repo_root=tmp_path,
    )


def test_validate_workflow_artifact_contracts_allows_transitive_needs(
    tmp_path: Path,
) -> None:
    """Allow consumers that reach producers through transitive job needs."""
    _write_file(tmp_path / "foo.txt")

    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
on: workflow_dispatch
jobs:
  producer:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/upload-artifact@v6
        with:
          name: foo
          path: foo.txt
  middle:
    needs: producer
    runs-on: ubuntu-latest
    steps:
      - run: "true"
  consumer:
    needs: middle
    runs-on: ubuntu-latest
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


def test_refresh_final_artifact_scope_rejects_non_update_paths(
    tmp_path: Path,
) -> None:
    """The PR artifact must not carry ordinary source files like flake.nix."""
    _write_file(tmp_path / "flake.lock")
    _write_file(tmp_path / "flake.nix")

    errors = contracts._validate_refresh_final_artifact_scope(
        {
            "merged-generated-formatted": contracts.ArtifactUpload(
                artifact_name="merged-generated-formatted",
                artifact_root="",
                job_id="refresh-sanity",
                job_instance_id="refresh-sanity",
                source_paths=("flake.lock", "flake.nix"),
                step_name="upload",
                stored_paths=("flake.lock", "flake.nix"),
            )
        },
        repo_root=tmp_path,
    )

    assert errors == [
        "Artifact `merged-generated-formatted` includes non-update path(s): `flake.nix`"
    ]


def test_refresh_final_artifact_scope_requires_generated_update_paths(
    tmp_path: Path,
) -> None:
    """The final PR artifact must include generated updater artifacts."""
    _write_file(tmp_path / "flake.lock")
    _write_file(tmp_path / "packages/demo/sources.json")
    _write_file(tmp_path / "packages/linear-cli/deno-deps.json")
    _write_file(tmp_path / "packages/neutils/build.zig.zon.nix")

    errors = contracts._validate_refresh_final_artifact_scope(
        {
            "merged-generated-formatted": contracts.ArtifactUpload(
                artifact_name="merged-generated-formatted",
                artifact_root="",
                job_id="refresh-sanity",
                job_instance_id="refresh-sanity",
                source_paths=("flake.lock", "packages/demo/sources.json"),
                step_name="upload",
                stored_paths=("flake.lock", "packages/demo/sources.json"),
            )
        },
        repo_root=tmp_path,
    )

    assert errors == [
        "Artifact `merged-generated-formatted` is missing update path(s): "
        "`packages/linear-cli/deno-deps.json`, "
        "`packages/neutils/build.zig.zon.nix`"
    ]


def test_validate_workflow_artifact_contracts_rejects_cyclic_needs(
    tmp_path: Path,
) -> None:
    """Reject workflows whose job dependency graph contains a cycle."""
    workflow_path = tmp_path / "workflow.yml"
    workflow_path.write_text(
        """
on: workflow_dispatch
jobs:
  alpha:
    needs: beta
    runs-on: ubuntu-latest
    steps:
      - run: "true"
  beta:
    needs: alpha
    runs-on: ubuntu-latest
    steps:
      - run: "true"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as exc_info:
        validate_workflow_artifact_contracts(
            workflow_path=workflow_path,
            repo_root=tmp_path,
        )
    message = str(exc_info.value)

    assert "contain a cycle" in message
    assert "alpha" in message
    assert "beta" in message


def test_update_refresh_workflow_artifact_contracts_hold() -> None:
    """Keep the checked-in refresh workflow artifact-safe."""
    validate_workflow_artifact_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update.yml",
        repo_root=REPO_ROOT,
    )


def test_update_certify_workflow_artifact_contracts_hold() -> None:
    """Keep the checked-in certification workflow artifact-safe."""
    validate_workflow_artifact_contracts(
        workflow_path=REPO_ROOT / ".github/workflows/update-certify.yml",
        repo_root=REPO_ROOT,
    )
