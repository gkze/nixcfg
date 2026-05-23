"""Tests for package-sliced update artifact helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.update.ci import update_target_artifacts as artifacts


def _inventory_payload() -> dict[str, object]:
    return {
        "targets": [
            {
                "name": "codex",
                "handles": {"sourceUpdate": True},
                "sourceTarget": {"path": "packages/codex/sources.json"},
                "generatedArtifacts": [
                    "packages/codex/Cargo.nix",
                    "packages/codex/crate-hashes.json",
                ],
            },
            {
                "name": "t3code-desktop",
                "handles": {"sourceUpdate": True},
                "sourceTarget": {"path": "packages/t3code-desktop/sources.json"},
                "generatedArtifacts": [],
            },
            {
                "name": "flake-only",
                "handles": {"sourceUpdate": False},
                "sourceTarget": None,
                "generatedArtifacts": [],
            },
        ]
    }


def test_build_matrix_slices_source_targets_and_platform_artifacts() -> None:
    """Only source updaters enter the matrix, with platform-specific paths."""
    matrix = artifacts.build_matrix(inventory=_inventory_payload())
    by_target = {entry["target"]: entry for entry in matrix["include"]}

    assert set(by_target) == {"codex", "t3code-desktop"}
    assert by_target["codex"]["artifact_paths_aarch64_linux"] == [
        "packages/codex/sources.json"
    ]
    assert by_target["codex"]["artifact_paths_x86_64_linux"] == [
        "packages/codex/sources.json",
        "packages/codex/Cargo.nix",
        "packages/codex/crate-hashes.json",
    ]
    assert by_target["t3code-desktop"]["artifact_paths_aarch64_darwin"] == [
        "packages/t3code-desktop/sources.json",
        "packages/t3code/bun.lock",
        "packages/t3code-desktop/bun.lock",
    ]
    assert by_target["t3code-desktop"]["regenerate_runtime_locks"] is True


def test_build_matrix_rejects_missing_targets_list() -> None:
    """Malformed inventory payloads fail before producing an empty matrix."""
    with pytest.raises(TypeError, match="targets list"):
        artifacts.build_matrix(inventory={})


def test_build_matrix_ignores_malformed_targets_and_unsafe_paths() -> None:
    """Matrix generation should ignore malformed entries and unsafe artifact paths."""
    matrix = artifacts.build_matrix(
        inventory={
            "targets": [
                "not an object",
                {"name": 1, "handles": {"sourceUpdate": True}},
                {"name": "bad-handles", "handles": []},
                {
                    "name": "unsafe",
                    "handles": {"sourceUpdate": True},
                    "sourceTarget": {"path": "../outside"},
                    "generatedArtifacts": ["/absolute", "safe/generated.txt", 1],
                },
                {
                    "name": "source-only",
                    "handles": {"sourceUpdate": True},
                    "sourceTarget": {"path": "packages/source-only/sources.json"},
                    "generatedArtifacts": "not a list",
                },
            ]
        }
    )

    entry = matrix["include"][0]
    assert entry["target"] == "unsafe"
    assert entry["artifact_paths_aarch64_darwin"] == []
    assert entry["artifact_paths_x86_64_linux"] == ["safe/generated.txt"]
    source_entry = matrix["include"][1]
    assert source_entry["target"] == "source-only"
    assert source_entry["artifact_paths_x86_64_linux"] == [
        "packages/source-only/sources.json"
    ]


def test_main_matrix_writes_stdout_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The matrix CLI can print or write the dynamic matrix JSON."""
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(_inventory_payload()), encoding="utf-8")
    output = tmp_path / "matrix.json"

    assert artifacts.main(["matrix", "--inventory", str(inventory)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["include"][0]["target"] == "codex"

    assert (
        artifacts.main([
            "matrix",
            "--inventory",
            str(inventory),
            "--output",
            str(output),
        ])
        == 0
    )
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["include"][1]["target"] == "t3code-desktop"


def test_main_matrix_rejects_non_object_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI reports malformed inventory JSON through main's error handler."""
    inventory = tmp_path / "inventory.json"
    inventory.write_text("[]\n", encoding="utf-8")

    assert artifacts.main(["matrix", "--inventory", str(inventory)]) == 1
    assert "Expected JSON object" in capsys.readouterr().err


def test_stage_artifact_copies_paths_and_records_missing_files(tmp_path: Path) -> None:
    """Staging preserves repo-relative paths and records expected misses."""
    root = tmp_path / "repo"
    (root / "packages/demo").mkdir(parents=True)
    (root / "packages/demo/sources.json").write_text("{}\n", encoding="utf-8")
    output = tmp_path / "artifact"

    status = artifacts.stage_artifact(
        paths=["packages/demo/sources.json", "packages/demo/Cargo.nix"],
        root=root,
        output=output,
        target="demo",
        platform="x86_64-linux",
        conclusion="success",
        exit_code=0,
    )

    assert (output / "packages/demo/sources.json").read_text(encoding="utf-8") == "{}\n"
    assert status["copiedPaths"] == ["packages/demo/sources.json"]
    assert status["missingPaths"] == ["packages/demo/Cargo.nix"]
    status_file = json.loads(
        (output / artifacts.STATUS_FILE_NAME).read_text(encoding="utf-8")
    )
    assert status_file["target"] == "demo"


def test_main_stage_accepts_path_alias_and_defaults_conclusion(tmp_path: Path) -> None:
    """The workflow-facing stage CLI derives failure from the exit code."""
    root = tmp_path / "repo"
    output = tmp_path / "artifact"
    (root / "packages/demo").mkdir(parents=True)
    (root / "packages/demo/sources.json").write_text("{}\n", encoding="utf-8")

    assert (
        artifacts.main([
            "stage",
            "--artifact-paths-json",
            '["packages/demo/sources.json"]',
            "--root",
            str(root),
            "--output",
            str(output),
            "--target",
            "demo",
            "--platform",
            "x86_64-linux",
            "--exit-code",
            "1",
        ])
        == 0
    )

    status = json.loads((output / artifacts.STATUS_FILE_NAME).read_text("utf-8"))
    assert status["conclusion"] == "failure"

    success_output = tmp_path / "success-artifact"
    assert (
        artifacts.main([
            "stage",
            "--artifact-paths-json",
            '["packages/demo/sources.json"]',
            "--root",
            str(root),
            "--output",
            str(success_output),
            "--target",
            "demo",
            "--platform",
            "x86_64-linux",
            "--exit-code",
            "0",
        ])
        == 0
    )
    success_status = json.loads(
        (success_output / artifacts.STATUS_FILE_NAME).read_text("utf-8")
    )
    assert success_status["conclusion"] == "success"

    baseline_output = tmp_path / "baseline-artifact"
    assert (
        artifacts.main([
            "stage",
            "--artifact-paths-json",
            '["packages/demo/sources.json"]',
            "--root",
            str(root),
            "--output",
            str(baseline_output),
            "--target",
            "demo",
            "--platform",
            "x86_64-linux",
            "--conclusion",
            "baseline",
        ])
        == 0
    )
    baseline_status = json.loads(
        (baseline_output / artifacts.STATUS_FILE_NAME).read_text("utf-8")
    )
    assert baseline_status["conclusion"] == "baseline"


def test_main_stage_rejects_non_list_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stage CLI validates the artifact path JSON shape."""
    assert (
        artifacts.main([
            "stage",
            "--paths-json",
            "{}",
            "--root",
            str(tmp_path),
            "--output",
            str(tmp_path / "artifact"),
            "--target",
            "demo",
            "--platform",
            "x86_64-linux",
            "--exit-code",
            "0",
        ])
        == 1
    )
    assert "Expected a JSON list" in capsys.readouterr().err


def test_aggregate_artifacts_applies_successes_and_collects_failures(
    tmp_path: Path,
) -> None:
    """Aggregation applies successful slices without copying failed slices."""
    artifacts_dir = tmp_path / "artifacts"
    success = artifacts_dir / "update-target-x86_64-linux-demo"
    failure = artifacts_dir / "update-target-x86_64-linux-bad"
    (success / "packages/demo").mkdir(parents=True)
    (failure / "packages/bad").mkdir(parents=True)
    (success / "packages/demo/sources.json").write_text("success\n", encoding="utf-8")
    (failure / "packages/bad/sources.json").write_text("failure\n", encoding="utf-8")
    (success / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": "demo",
            "conclusion": "success",
            "copiedPaths": ["packages/demo/sources.json"],
        }),
        encoding="utf-8",
    )
    (failure / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": "bad",
            "conclusion": "failure",
            "copiedPaths": ["packages/bad/sources.json"],
        }),
        encoding="utf-8",
    )
    output_root = tmp_path / "repo"
    status_output = tmp_path / "status.json"

    collection = artifacts.aggregate_artifacts(
        artifacts_dir=artifacts_dir,
        output_root=output_root,
        platform="x86_64-linux",
        status_output=status_output,
        required_platforms=("x86_64-linux",),
    )

    assert (output_root / "packages/demo/sources.json").read_text(
        encoding="utf-8"
    ) == "success\n"
    assert not (output_root / "packages/bad/sources.json").exists()
    assert [item["target"] for item in collection["targets"]] == ["bad", "demo"]
    assert json.loads(status_output.read_text(encoding="utf-8"))["kind"] == (
        artifacts.STATUS_COLLECTION_KIND
    )


def test_main_aggregate_accepts_output_alias_and_default_status(
    tmp_path: Path,
) -> None:
    """The workflow-facing aggregate CLI writes status inside the output root."""
    artifacts_dir = tmp_path / "artifacts"
    success = artifacts_dir / "update-target-x86_64-linux-demo"
    (success / "packages/demo").mkdir(parents=True)
    (success / "packages/demo/sources.json").write_text("success\n", encoding="utf-8")
    (success / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": "demo",
            "conclusion": "success",
            "copiedPaths": ["packages/demo/sources.json"],
        }),
        encoding="utf-8",
    )
    output_root = tmp_path / "repo"

    assert (
        artifacts.main([
            "aggregate",
            "--artifacts-dir",
            str(artifacts_dir),
            "--output",
            str(output_root),
            "--platform",
            "x86_64-linux",
            "--required-platform",
            "x86_64-linux",
        ])
        == 0
    )

    assert (output_root / "packages/demo/sources.json").read_text(
        "utf-8"
    ) == "success\n"
    status = json.loads((output_root / artifacts.STATUS_FILE_NAME).read_text("utf-8"))
    assert status["eligibleTargets"] == ["demo"]


def test_aggregate_artifacts_requires_all_platforms_for_target(tmp_path: Path) -> None:
    """A target with a failed platform slice is not partially materialized."""
    artifacts_dir = tmp_path / "artifacts"
    success = artifacts_dir / "update-target-x86_64-linux-demo"
    failure = artifacts_dir / "update-target-aarch64-linux-demo"
    (success / "packages/demo").mkdir(parents=True)
    failure.mkdir(parents=True)
    (success / "packages/demo/sources.json").write_text("success\n", encoding="utf-8")
    (success / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": "demo",
            "conclusion": "success",
            "copiedPaths": ["packages/demo/sources.json"],
        }),
        encoding="utf-8",
    )
    (failure / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "aarch64-linux",
            "target": "demo",
            "conclusion": "failure",
            "copiedPaths": ["packages/demo/sources.json"],
        }),
        encoding="utf-8",
    )
    output_root = tmp_path / "repo"

    collection = artifacts.aggregate_artifacts(
        artifacts_dir=artifacts_dir,
        output_root=output_root,
        platform="x86_64-linux",
        status_output=tmp_path / "status.json",
        required_platforms=("x86_64-linux", "aarch64-linux"),
    )

    assert collection["eligibleTargets"] == []
    assert not (output_root / "packages/demo/sources.json").exists()


def test_aggregate_artifacts_skips_malformed_and_missing_copied_paths(
    tmp_path: Path,
) -> None:
    """Aggregation should ignore malformed statuses and copied paths that are unsafe."""
    artifacts_dir = tmp_path / "artifacts"
    good = artifacts_dir / "update-target-x86_64-linux-good"
    malformed = artifacts_dir / "update-target-x86_64-linux-malformed"
    good.mkdir(parents=True)
    malformed.mkdir(parents=True)
    (good / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": "good",
            "conclusion": "success",
            "copiedPaths": [
                "../unsafe",
                "packages/good/missing.json",
            ],
        }),
        encoding="utf-8",
    )
    (malformed / artifacts.STATUS_FILE_NAME).write_text(
        json.dumps({
            "platform": "x86_64-linux",
            "target": 1,
            "conclusion": "success",
            "copiedPaths": ["packages/bad/sources.json"],
        }),
        encoding="utf-8",
    )

    collection = artifacts.aggregate_artifacts(
        artifacts_dir=artifacts_dir,
        output_root=tmp_path / "repo",
        platform="x86_64-linux",
        status_output=tmp_path / "status.json",
        required_platforms=("x86_64-linux",),
    )

    assert collection["eligibleTargets"] == ["good"]
    assert collection["targets"][0]["target"] == 1


def test_aggregate_artifacts_requires_matching_statuses(tmp_path: Path) -> None:
    """A platform aggregation with no target reports should fail loudly."""
    with pytest.raises(RuntimeError, match="No update target statuses"):
        artifacts.aggregate_artifacts(
            artifacts_dir=tmp_path,
            output_root=tmp_path / "repo",
            platform="x86_64-linux",
            status_output=tmp_path / "status.json",
        )


def test_update_target_artifacts_main_reports_argument_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI error adapter should convert validation errors to exit code 1."""
    assert artifacts.main(["matrix", "--inventory", "/missing/inventory.json"]) == 1
    assert "No such file" in capsys.readouterr().err
