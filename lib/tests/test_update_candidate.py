"""Portable candidate behavior through real Git workspaces and updater phases."""

import json
import subprocess
import traceback
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
from lib.tests._run_updates_helpers import drain_events, make_run_plan
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.tests._updater_helpers import load_repo_module_for_test
from lib.update import cli, source_runner
from lib.update.candidate import Candidate, Preparation, ResolvedVersion, git
from lib.update.ci import candidate as pipeline
from lib.update.derivation_validation import (
    DerivationValidation,
    DerivationValidationFailure,
)
from lib.update.persistence import IsolatedUpdateWorkspace
from lib.update.ui_consumer import consume_events
from lib.update.updaters import Updater, VersionInfo
from lib.update.updaters.metadata import GitHubReleaseMetadata, MappingMetadata

_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


@dataclass(frozen=True)
class ExampleMetadata(MappingMetadata):
    """Representative nested updater metadata, including non-mapping state."""

    hashes: tuple[str, ...]
    source: SourceEntry


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"commit": "abc", "nested": [1, True]},
        GitHubReleaseMetadata(tag="v2"),
        ExampleMetadata(hashes=(_HASH,), source=SourceEntry(version="2", hashes={})),
    ],
)
def test_resolution_survives_json_without_losing_metadata(metadata) -> None:
    """Typed/custom metadata remains usable by the original updater."""
    info = VersionInfo(version="2", metadata=metadata)
    saved = ResolvedVersion.capture(info)
    loaded = ResolvedVersion.model_validate_json(saved.model_dump_json())
    assert loaded.restore() == info


def test_resolution_rejects_executable_or_unknown_metadata() -> None:
    """Artifact metadata cannot request arbitrary Python deserialization."""
    with pytest.raises(ValueError, match="invalid-json-value"):
        ResolvedVersion.capture(VersionInfo(version="2", metadata=object()))
    with pytest.raises(ValueError, match="Unknown updater metadata"):
        ResolvedVersion(version="2", metadata_type="os.system").restore()


@pytest.mark.parametrize(
    ("module_path", "class_name", "extra"),
    [
        (
            "packages/emdash/updater.py",
            "EmdashSourceMetadata",
            {
                "shell_env_capture_path": "src/env.ts",
                "toolchain": {
                    "node_engine": ">=24.0.0",
                    "nodejs_attr": "nodejs_24",
                    "nodejs_version": "24.20.0",
                    "package_manager": "pnpm@10.28.2",
                    "pnpm_engine": ">=10.28.0",
                    "pnpm_attr": "pnpm_10",
                    "pnpm_version": "10.34.5",
                },
            },
        ),
        ("packages/mux/updater.py", "MuxSourceMetadata", {"bun_version": "1.3.0"}),
        (
            "lib/update/electron_manifest.py",
            "ElectronManifestMetadata",
            {"manifest_path": "package.json", "manifest_version": "1.2.3"},
        ),
    ],
)
def test_package_metadata_survives_native_candidate_handoff(
    module_path, class_name, extra
) -> None:
    """Real dynamic metadata must resolve nested model types at runtime."""
    module = load_repo_module_for_test(module_path, prefix="candidate_metadata")
    saved = ResolvedVersion(
        version="1.2.3",
        metadata_type=f"{module.__name__}.{class_name}",
        metadata={
            "node": {"locked": {"type": "github", "rev": "a" * 40, "narHash": _HASH}},
            "commit": "a" * 40,
            "electron_version": "40.10.2",
            **extra,
        },
    )
    restored = saved.restore()
    captured = ResolvedVersion.capture(restored)
    assert ResolvedVersion.model_validate_json(
        captured.model_dump_json()
    ).restore() == (restored)
    assert isinstance(restored.metadata, MappingMetadata)
    node = restored.metadata["node"]
    assert isinstance(node, FlakeLockNode)
    assert node.locked is not None
    assert node.locked.rev == "a" * 40


@pytest.fixture
def prepared_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, list[str], dict[str, str | bool]]:
    """Keep Git, candidate isolation, scheduling and persistence real."""
    root = tmp_path / "live"
    initial = SourceEntry(version="1", hashes={}).model_dump_json(by_alias=True)
    init_update_workspace_repo(
        root,
        tracked_files={
            "packages/example/sources.json": initial,
            "packages/example/updater.py": "# fixture\n",
        },
    )
    operations: list[str] = []
    state = {"system": "aarch64-darwin", "fail": False}

    class Example(Updater):
        name = "example"

        async def fetch_latest(self, session, *, context):
            _ = session, context
            operations.append("resolve")
            return VersionInfo(version="2", metadata=GitHubReleaseMetadata(tag="v2"))

        async def fetch_hashes(self, info, session, *, context, emit):
            _ = session, emit
            context.hashes_fully_computed = False
            assert info.metadata == GitHubReleaseMetadata(tag="v2")
            operations.append(f"hash:{state['system']}")
            if state.get("darwin_only") and state["system"] != "aarch64-darwin":
                return {"aarch64-darwin": _HASH}
            if state["fail"]:
                msg = "upstream packaging changed"
                raise RuntimeError(msg)
            return {str(state["system"]): _HASH}

    registry = {"example": Example}

    def plan(_opts):
        plan = make_run_plan(source_names=("example",), dry_run=True)
        plan.sources.entries["example"] = SourceEntry.model_validate_json(
            Path("packages/example/sources.json").read_bytes()
        )
        return plan

    monkeypatch.setattr(cli, "get_repo_root", lambda: root)
    monkeypatch.setattr(cli, "_build_run_plan", plan)
    monkeypatch.setattr(cli, "_get_updaters", lambda: registry)
    monkeypatch.setattr(source_runner, "_get_updaters", lambda: registry)
    monkeypatch.setattr(cli, "consume_events", drain_events)
    monkeypatch.setattr(cli, "_handle_required_tool_check", lambda _opts: None)
    monkeypatch.setattr(pipeline, "get_current_nix_platform", lambda: state["system"])
    monkeypatch.setattr(pipeline, "get_repo_root", lambda: root)
    monkeypatch.setattr(pipeline, "ensure_updaters_loaded", lambda: registry)
    return root, operations, state


@pytest.mark.parametrize("darwin_only", [False, True])
def test_native_stages_pin_versions_preserve_hashes_and_never_promote(
    prepared_run,
    darwin_only: bool,
) -> None:
    """Each runner reconstructs the candidate from the untouched original tree."""
    root, operations, state = prepared_run
    state["darwin_only"] = darwin_only
    previous = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, status = pipeline.prepare_candidate(("example",), previous=previous)
        assert status == 0
        assert candidate.prepared
        previous = Candidate.model_validate_json(candidate.model_dump_json())
        assert not git(root, "status", "--porcelain")
    assert operations.count("resolve") == 1
    assert len(operations) == 4
    assert previous is not None
    with IsolatedUpdateWorkspace(root) as workspace:
        previous.apply(workspace.root)
        result = SourceEntry.model_validate_json(
            (workspace.root / "packages/example/sources.json").read_bytes()
        )
        assert result.version == "2"
        assert set(result.hashes.mapping) == (
            {"aarch64-darwin"} if darwin_only else set(pipeline.supported_systems())
        )


def test_preparation_streams_progress_separately_from_json(
    prepared_run, monkeypatch, capsys
) -> None:
    """The CI consumer gets useful progress and a parseable result separately."""
    monkeypatch.setattr(cli, "consume_events", consume_events)
    _, status = pipeline.prepare_candidate(("example",))
    captured = capsys.readouterr()
    assert status == 0
    assert json.loads(captured.out)["success"] is True
    assert "Phase" in captured.err
    assert "example" in captured.err


def test_failed_preparation_retains_resolution_and_is_not_a_completed_platform(
    prepared_run,
) -> None:
    """Repair evidence survives a domain failure, without certifying or promoting it."""
    root, operations, state = prepared_run
    state["fail"] = True
    candidate, status = pipeline.prepare_candidate(("example",))
    assert status == 1
    assert not candidate.prepared
    assert candidate.systems == ()
    assert candidate.resolutions["example"].version == "2"
    assert operations == ["resolve", "hash:aarch64-darwin"]
    assert not git(root, "status", "--porcelain")
    with pytest.raises(ValueError, match="failed preparation"):
        Preparation(system="x86_64-linux", targets=("example",), previous=candidate)


def test_candidate_identity_and_stage_admission(prepared_run) -> None:
    """Reject corruption, different baselines, duplicate stages and retargeting."""
    root, _, _ = prepared_run
    candidate, _ = pipeline.prepare_candidate(("example",))
    for updates, message in [
        ({"base_tree": "0" * 40}, "baseline"),
        ({"tree": "0" * 40}, "recorded tree"),
    ]:
        with (
            IsolatedUpdateWorkspace(root) as workspace,
            pytest.raises(ValueError, match=message),
        ):
            candidate.model_copy(update=updates).apply(workspace.root)
    with pytest.raises(ValueError, match="already prepared"):
        Preparation(system="aarch64-darwin", targets=("example",), previous=candidate)
    with pytest.raises(ValueError, match="target selection"):
        Preparation(system="x86_64-linux", targets=("other",), previous=candidate)


@pytest.mark.parametrize("validate_all_packages", [False, True])
@pytest.mark.parametrize("no_change", [False, True])
def test_repair_inventory_blocks_unselected_package_failure(
    prepared_run, monkeypatch, validate_all_packages, no_change
) -> None:
    """Repair admission includes held declarations even without update changes."""
    _, operations, state = prepared_run
    registry = pipeline.ensure_updaters_loaded()

    class Held(Updater):
        name = "held"
        bulk_update_hold = "Keep the pinned release"
        derivation_validations = (
            DerivationValidation(
                installable=".#pkgs.{system}.held",
                systems=pipeline.supported_systems(),
                mode="build",
            ),
        )

    registry["held"] = Held
    candidate = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, status = pipeline.prepare_candidate(
            ("example",),
            previous=candidate,
            validate_all_packages=validate_all_packages if candidate is None else False,
        )
        assert status == 0
        candidate = Candidate.model_validate_json(candidate.model_dump_json())
        assert candidate.sources == ("example",)
        assert candidate.targets == ("example",)
        assert candidate.validate_all_packages == validate_all_packages
    assert candidate is not None
    assert operations.count("resolve") == 1
    if no_change:
        candidate = candidate.model_copy(
            update={
                "tree": candidate.base_tree,
                "patch": b"",
                "sources": (),
            }
        )
    checked = []

    def validate_requests(requests, **_kwargs):
        checked.extend(requests)
        return tuple(
            DerivationValidationFailure(
                request.source, request.installable, "broken repair"
            )
            for request in requests
        )

    monkeypatch.setattr(
        pipeline.validation, "get_current_nix_platform", lambda: state["system"]
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_derivation_requests", validate_requests
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_root_closures", lambda **_kwargs: ()
    )
    reports = []
    for system in pipeline.supported_systems():
        state["system"] = system
        report = pipeline.validate_candidate(candidate)
        reports.append(report)
        assert report.validate_all_packages == validate_all_packages
        assert bool(report.failures) == validate_all_packages
    if validate_all_packages:
        assert [request.installable for request in checked] == [
            f".#pkgs.{system}.held" for system in pipeline.supported_systems()
        ]
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, reports)
        targeted_reports = [
            report.model_copy(
                update={
                    "failures": (),
                    "validate_all_packages": False,
                }
            )
            for report in reports
        ]
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, targeted_reports)
    else:
        assert checked == []
        assert pipeline.certified_patch(candidate, reports) == candidate.patch


def test_dependent_resolution_is_recomputed() -> None:
    """A companion consumes current prerequisite outputs instead of old metadata."""
    preparation = Preparation(system="aarch64-darwin", targets=())
    preparation.dependent_sources.add("companion")
    preparation.record("companion", VersionInfo(version="2"))
    preparation.record("absent", None)
    assert preparation.resolved("companion") is None
    assert preparation.resolved("absent") is None
    assert preparation.resolutions == {}


def test_native_validation_and_certification(
    prepared_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only matching successful evidence from every builder authorizes a patch."""
    root, _, state = prepared_run
    candidate = None
    for system in pipeline.supported_systems():
        state["system"] = system
        candidate, _ = pipeline.prepare_candidate(("example",), previous=candidate)
    assert candidate is not None
    roots: list[tuple[str, ...]] = []

    def validate_sources(names, *, flake_root, **_kwargs):
        assert names == ("example",)
        assert (
            json.loads((flake_root / "packages/example/sources.json").read_text())[
                "version"
            ]
            == "2"
        )
        return ()

    def validate_roots(*, systems, include_dependencies, **_kwargs):
        assert include_dependencies
        roots.append(systems)
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_derivations", validate_sources)
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", validate_roots)
    reports = []
    for system in pipeline.supported_systems():
        state["system"] = system
        reports.append(pipeline.validate_candidate(candidate))
    assert roots == [(system,) for system in pipeline.supported_systems()]
    assert pipeline.certified_patch(candidate, reports) == candidate.patch
    assert not git(root, "status", "--porcelain")
    for bad in (
        reports[:-1],
        [*reports, reports[0]],
        [reports[0].model_copy(update={"tree": "wrong"}), *reports[1:]],
        [
            reports[0].model_copy(
                update={
                    "failures": (
                        DerivationValidationFailure(
                            "example", ".#example", "build failed"
                        ),
                    )
                }
            ),
            *reports[1:],
        ],
    ):
        with pytest.raises(ValueError, match="Validation reports"):
            pipeline.certified_patch(candidate, bad)
    with pytest.raises(ValueError, match="every configured system"):
        pipeline.certified_patch(candidate.model_copy(update={"systems": ()}), reports)


def test_prepare_command_exports_failure_evidence_outside_checkout(
    prepared_run, tmp_path: Path
) -> None:
    """The CLI emits machine-readable diagnostics and saves a failed candidate."""
    root, _, state = prepared_run
    state["fail"] = True
    output = tmp_path / "artifacts" / "candidate.json"
    result = CliRunner().invoke(
        pipeline.app, ["prepare", "--output", str(output), "example"]
    )
    assert result.exit_code == 1, result.output
    assert not Candidate.model_validate_json(output.read_bytes()).prepared
    assert json.loads(result.stdout)["errors"] == ["example"]
    rejected = CliRunner().invoke(
        pipeline.app, ["prepare", "--output", str(root / "candidate.json")]
    )
    assert rejected.exit_code != 0
    assert not (root / "candidate.json").exists()


@pytest.mark.parametrize("validate_all_packages", [False, True])
def test_candidate_commands_transfer_the_pinned_selection_and_native_reports(
    validate_all_packages,
    prepared_run,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the actual CLI files passed between independent native jobs."""
    _, _, state = prepared_run
    runner = CliRunner()
    previous = None
    reports = []
    for index, system in enumerate(pipeline.supported_systems()):
        state["system"] = system
        output = tmp_path / f"candidate-{index}.json"
        args = ["prepare", "--output", str(output)]
        if previous is None:
            if validate_all_packages:
                args.append("--validate-all-packages")
            args.append("example")
        else:
            args += ["--previous", str(previous)]
        result = runner.invoke(pipeline.app, args)
        assert result.exit_code == 0, result.output
        assert Candidate.model_validate_json(output.read_bytes()).targets == (
            "example",
        )
        previous = output
        assert (
            Candidate.model_validate_json(output.read_bytes()).validate_all_packages
            == validate_all_packages
        )
    assert previous is not None
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )
    monkeypatch.setattr(
        pipeline.validation, "validate_root_closures", lambda **_kwargs: ()
    )
    for system in pipeline.supported_systems():
        state["system"] = system
        report = tmp_path / f"{system}.json"
        result = runner.invoke(
            pipeline.app,
            ["validate", "--candidate", str(previous), "--output", str(report)],
        )
        assert result.exit_code == 0, result.exception
        reports.append(report)
    patch = tmp_path / "update.patch"
    args = ["certify", "--candidate", str(previous), "--output", str(patch)]
    for report in reports:
        args += ["--report", str(report)]
    result = runner.invoke(pipeline.app, args)
    assert result.exit_code == 0, result.exception
    assert (
        patch.read_bytes() == Candidate.model_validate_json(previous.read_bytes()).patch
    )
    reports[0].unlink()
    assert runner.invoke(pipeline.app, args).exit_code != 0


def test_noop_candidate_still_validates_repaired_baseline_roots(
    prepared_run,
    monkeypatch,
) -> None:
    """A repaired baseline can be broken even when preparation emits no patch."""
    root, _, state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    checked = []
    monkeypatch.setattr(
        pipeline.validation, "validate_derivations", lambda *_args, **_kwargs: ()
    )

    def roots(**kwargs):
        checked.append(kwargs["systems"])
        return ()

    monkeypatch.setattr(pipeline.validation, "validate_root_closures", roots)
    assert pipeline.validate_candidate(candidate).failures == ()
    assert checked == [(state["system"],)]


@pytest.mark.parametrize(
    ("systems", "prepared"),
    [((), True), (("aarch64-darwin", "aarch64-darwin"), True), ((), False)],
)
def test_incomplete_candidates_never_reach_validation(systems, prepared) -> None:
    candidate = Candidate(
        base_tree="a" * 40,
        tree="a" * 40,
        targets=(),
        sources=(),
        systems=systems,
        resolutions={},
        prepared=prepared,
        patch=b"",
    )
    with pytest.raises(ValueError, match="every configured system"):
        pipeline.validate_candidate(candidate)


def test_unsupported_builder_and_early_preparation_failure_are_explicit(
    prepared_run, monkeypatch
) -> None:
    _, _, state = prepared_run
    state["system"] = "unsupported"
    with pytest.raises(ValueError, match="No configured builder policy"):
        pipeline.prepare_candidate(())
    candidate = Candidate(
        base_tree="a" * 40,
        tree="a" * 40,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )
    with pytest.raises(ValueError, match="Unexpected validation platform"):
        pipeline.validate_candidate(candidate)
    state["system"] = "aarch64-darwin"

    async def fail(*_args, **_kwargs):
        return 1

    monkeypatch.setattr(pipeline.update_cli, "collect_run_outcome", fail)
    with pytest.raises(RuntimeError, match="before a candidate could be captured"):
        pipeline.prepare_candidate(())


@pytest.mark.parametrize("phase", ["packages", "roots"])
def test_incomplete_native_validation_cannot_issue_report(
    prepared_run, monkeypatch, tmp_path, phase
) -> None:
    """The CI command fails before writing certification evidence on incompleteness."""
    root, _, _state = prepared_run
    tree = git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    candidate = Candidate(
        base_tree=tree,
        tree=tree,
        targets=(),
        sources=(),
        systems=pipeline.supported_systems(),
        resolutions={},
        prepared=True,
        patch=b"",
    )

    def incomplete(*_args, **_kwargs):
        def run(args, **_kwargs):
            raise subprocess.TimeoutExpired(
                args, 1, stderr="https://example.test/?token=synthetic-ci-secret"
            )

        return pipeline.validation._run_validation_command(
            ["nix", "build", ".#demo"],
            cwd=root,
            timeout=1,
            run=run,
            sleep=lambda _: pytest.fail("incomplete execution must not retry"),
        )

    monkeypatch.setattr(
        pipeline.validation,
        "validate_derivations",
        incomplete if phase == "packages" else lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(pipeline.validation, "validate_root_closures", incomplete)
    source = tmp_path / "candidate.json"
    source.write_text(candidate.model_dump_json())
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        pipeline.app,
        [
            "validate",
            "--candidate",
            str(source),
            "--output",
            str(report),
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, pipeline.validation.ValidationIncompleteError)
    assert "synthetic-ci-secret" not in "".join(
        traceback.format_exception(result.exception)
    )
    assert not report.exists()
    with pytest.raises(ValueError, match="Validation reports"):
        pipeline.certified_patch(candidate, [])
