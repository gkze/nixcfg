"""Unit tests for source-update merge behavior in the update CLI."""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._run_updates_helpers import drain_events, make_run_plan
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli import (
    OutputOptions,
    UpdateOptions,
    UpdateSummary,
    _emit_summary,
    run_updates,
)
from lib.update.cli_inventory import (
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.persistence import merge_source_updates
from lib.update.refs import FlakeInputRef
from lib.update.source_runner import UpdatePhaseResult
from lib.update.updaters import Updater


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


class _CapturedOut(Protocol):
    out: str
    err: str


class _CaptureLike(Protocol):
    def readouterr(self) -> _CapturedOut: ...


class _PassthroughUpdateWorkspace:
    """Keep lower-level orchestration tests focused on their existing seam."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self) -> _PassthroughUpdateWorkspace:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def promote(self, _allowed: object) -> tuple[Path, ...]:
        return ()


def _use_passthrough_workspace(monkeypatch: _MonkeyPatchLike) -> None:
    monkeypatch.setattr(
        "lib.update.persistence.IsolatedUpdateWorkspace",
        _PassthroughUpdateWorkspace,
    )


def _entry_with_hashes(*entries: HashEntry) -> SourceEntry:
    return SourceEntry(hashes=HashCollection(entries=list(entries)))


def test_merge_source_updates_native_only_preserves_other_platform_hashes() -> None:
    """Merge native hashes when updater-owned source identity is unchanged."""
    existing = {
        "opencode": SourceEntry(
            commit="a" * 40,
            hashes=HashCollection(
                entries=[
                    HashEntry.create(
                        hash_type="nodeModulesHash",
                        hash_value="sha256-JnkqDwuC7lNsjafV+jOGfvs8K1xC8rk5CTOW+spjiCA=",
                        platform="aarch64-darwin",
                    ),
                    HashEntry.create(
                        hash_type="nodeModulesHash",
                        hash_value="sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M=",
                        platform="x86_64-linux",
                    ),
                ]
            ),
            input="opencode",
            pins={"runtimeVersion": "1.0.0"},
            urls={"x86_64-linux": "https://example.invalid/linux.tar.gz"},
        ),
    }
    updates = {
        "opencode": SourceEntry(
            hashes=HashCollection(
                entries=[
                    HashEntry.create(
                        hash_type="nodeModulesHash",
                        hash_value="sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
                        platform="aarch64-darwin",
                    ),
                ]
            ),
            pins={"runtimeVersion": "1.0.0"},
        ),
    }

    merged = merge_source_updates(existing, updates, native_only=True)

    result_entries = merged["opencode"].hashes.entries
    if result_entries is None:
        raise AssertionError
    values_by_platform = {entry.platform: entry.hash for entry in result_entries}
    assert values_by_platform == {
        "aarch64-darwin": "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
        "x86_64-linux": "sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M=",
    }
    assert merged["opencode"].commit == "a" * 40
    assert merged["opencode"].input == "opencode"
    assert merged["opencode"].pins == {"runtimeVersion": "1.0.0"}
    assert merged["opencode"].urls == {
        "x86_64-linux": "https://example.invalid/linux.tar.gz",
    }


def test_merge_source_updates_native_only_replaces_single_platform_pins() -> None:
    """Delete stale pins when a native update replaces the complete hash set."""
    existing = {
        "demo": SourceEntry(
            hashes={"aarch64-darwin": "sha256-oldDarwin"},
            pins={"removed": "obsolete", "runtimeVersion": "1.0.0"},
        )
    }
    updates = {
        "demo": SourceEntry(
            hashes={"aarch64-darwin": "sha256-newDarwin"},
            pins={"runtimeVersion": "2.0.0"},
        )
    }

    merged = merge_source_updates(existing, updates, native_only=True)

    assert merged["demo"].hashes.mapping == {"aarch64-darwin": "sha256-newDarwin"}
    assert merged["demo"].pins == {"runtimeVersion": "2.0.0"}


def test_merge_source_updates_non_native_returns_updates_unchanged() -> None:
    """Return updates unchanged when native-only merge mode is disabled."""
    updates = {
        "demo": _entry_with_hashes(
            HashEntry.create(
                hash_type="sha256",
                hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
            ),
        ),
    }

    merged = merge_source_updates({}, updates, native_only=False)

    assert merged is updates


def test_run_updates_list_json_outputs_sources_and_inputs(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit machine-readable inventory payload for list mode."""
    monkeypatch.setattr(
        "lib.update.cli_inventory.build_update_inventory",
        lambda: [
            _InventoryTarget(
                name="tool",
                handles=_InventoryHandles(
                    ref_update=True,
                    input_refresh=False,
                    source_update=False,
                    artifact_write=False,
                ),
                classification="refOnly",
                backing_input="tool",
                ref_target=_InventoryRefTarget(
                    input_name="tool",
                    source_type="github",
                    owner="owner",
                    repo="repo",
                    selector="v1.2.3",
                    locked_rev="abc123",
                ),
                source_target=None,
                generated_artifacts=(),
            ),
            _InventoryTarget(
                name="alpha",
                handles=_InventoryHandles(
                    ref_update=False,
                    input_refresh=False,
                    source_update=True,
                    artifact_write=False,
                ),
                classification="sourceOnly",
                backing_input=None,
                ref_target=None,
                source_target=_InventorySourceTarget(
                    path="packages/alpha/sources.json",
                    version="1.0.0",
                    commit=None,
                    hash_kinds=("sha256",),
                    updater_kind="download",
                    updater_class="AlphaUpdater",
                ),
                generated_artifacts=(),
            ),
        ],
    )

    opts = UpdateOptions(list_targets=True, json=True)
    exit_code = asyncio.run(run_updates(opts))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schemaVersion"] == 1
    assert payload["kind"] == "nixcfg-update-inventory"
    assert payload["summary"]["totalTargets"] == 2
    assert payload["summary"]["counts"]["refOnly"] == 1
    assert payload["summary"]["counts"]["sourceOnly"] == 1
    assert payload["summary"]["counts"]["refAndSource"] == 0
    assert payload["summary"]["counts"]["unclassified"] == 0
    assert payload["targets"] == [
        {
            "name": "alpha",
            "handles": {
                "refUpdate": False,
                "inputRefresh": False,
                "sourceUpdate": True,
                "artifactWrite": False,
            },
            "classification": "sourceOnly",
            "backingInput": None,
            "refTarget": None,
            "sourceTarget": {
                "path": "packages/alpha/sources.json",
                "version": "1.0.0",
                "commit": None,
                "hashKinds": ["sha256"],
                "updaterKind": "download",
                "updaterClass": "AlphaUpdater",
            },
            "generatedArtifacts": [],
        },
        {
            "name": "tool",
            "handles": {
                "refUpdate": True,
                "inputRefresh": False,
                "sourceUpdate": False,
                "artifactWrite": False,
            },
            "classification": "refOnly",
            "backingInput": "tool",
            "refTarget": {
                "input": "tool",
                "sourceType": "github",
                "owner": "owner",
                "repo": "repo",
                "selector": "v1.2.3",
                "lockedRev": "abc123",
            },
            "sourceTarget": None,
            "generatedArtifacts": [],
        },
    ]


def test_run_updates_schema_outputs_json(capsys: _CaptureLike) -> None:
    """Emit sources.json JSON schema and succeed."""
    opts = UpdateOptions(schema=True)
    exit_code = asyncio.run(run_updates(opts))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "object"
    assert payload["additionalProperties"] == {"$ref": "#/$defs/SourceEntry"}


def test_run_updates_validate_json_outputs_success(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit validation success details for json mode."""
    monkeypatch.setattr(
        "lib.update.sources.load_all_sources",
        lambda: SimpleNamespace(entries={}),
    )
    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency",
        lambda: None,
    )

    opts = UpdateOptions(validate=True, json=True)
    exit_code = asyncio.run(run_updates(opts))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"valid": True, "sources": 0}


def test_run_updates_validate_json_outputs_error(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit validation error details for json mode and fail."""
    monkeypatch.setattr(
        "lib.update.sources.load_all_sources",
        lambda: SimpleNamespace(entries={}),
    )

    def _boom() -> None:
        msg = "bad metadata"
        raise ValueError(msg)

    monkeypatch.setattr(
        "lib.update.sources.validate_source_discovery_consistency", _boom
    )

    opts = UpdateOptions(validate=True, json=True)
    exit_code = asyncio.run(run_updates(opts))

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert "bad metadata" in payload["error"]


def test_run_updates_persists_before_derivation_validation_failure(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Finish persistence, then fail even a no-op update on broken evaluation."""
    _use_passthrough_workspace(monkeypatch)

    class _ValidatingUpdater(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    plan = make_run_plan(source_names=("demo",))
    events: list[str] = []

    def _persist(**_kwargs: object) -> None:
        events.append("persist")

    def _run_nix_eval(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert events == ["persist"]
        events.append("validate")
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="error: attribute 'missing-member' missing",
        )

    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(
            0,
            result=UpdatePhaseResult(details={"demo": "no_change"}),
        ),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        _persist,
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr("subprocess.run", _run_nix_eval)

    exit_code = asyncio.run(run_updates(UpdateOptions(targets=("demo",))))

    assert exit_code == 1
    assert events == ["persist", "validate"]
    captured = capsys.readouterr()
    assert "Failed: demo" in captured.err
    assert "attribute 'missing-member' missing" in captured.err


def test_run_updates_skips_derivation_validation_after_phase_errors(
    monkeypatch: _MonkeyPatchLike,
) -> None:
    """Do not evaluate a candidate tree left incomplete by update errors."""
    _use_passthrough_workspace(monkeypatch)

    class _ValidatingUpdater(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    def _unexpected_eval(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("incomplete update must not evaluate derivations")

    plan = make_run_plan(source_names=("demo",))
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(
            0,
            result=UpdatePhaseResult(details={"demo": "error"}),
        ),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr("subprocess.run", _unexpected_eval)

    assert asyncio.run(run_updates(UpdateOptions(targets=("demo",)))) == 1


def test_run_updates_preserves_phase_error_priority_and_skips_validation(
    monkeypatch: _MonkeyPatchLike,
    tmp_path: Path,
) -> None:
    """A later source success cannot hide a ref error or trigger validation."""
    _use_passthrough_workspace(monkeypatch)
    flake_nix = tmp_path / "flake.nix"
    flake_lock = tmp_path / "flake.lock"
    source_file = tmp_path / "packages" / "good" / "sources.json"
    artifact_file = tmp_path / "packages" / "good" / "generated.nix"
    flake_nix.write_text("flake before\n", encoding="utf-8")
    flake_lock.write_text("lock before\n", encoding="utf-8")
    ref = FlakeInputRef("demo", "owner", "repo", "v1", "github")
    plan = make_run_plan(source_names=("demo", "good"), ref_inputs=(ref,))

    async def _run_ref_phase(**_kwargs: object) -> UpdatePhaseResult:
        flake_nix.write_text("flake after\n", encoding="utf-8")
        flake_lock.write_text("lock after\n", encoding="utf-8")
        return UpdatePhaseResult(details={"demo": "error"})

    async def _run_sources_phase(_context: object) -> UpdatePhaseResult:
        entry = SourceEntry(hashes={"x86_64-linux": "sha256-updated"})
        return UpdatePhaseResult(
            details={"demo": "updated", "good": "updated"},
            source_updates={"demo": entry, "good": entry},
            artifact_updates={
                "good": (GeneratedArtifact.text(artifact_file, "generated\n"),)
            },
        )

    def _persist(**_kwargs: object) -> tuple[Path, Path]:
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("source after\n", encoding="utf-8")
        artifact_file.write_text("artifact after\n", encoding="utf-8")
        return source_file, artifact_file

    def _unexpected_validation(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("a phase error must skip derivation validation")

    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: plan)
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr("lib.update.source_runner.run_ref_phase", _run_ref_phase)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase", _run_sources_phase
    )
    monkeypatch.setattr(
        "lib.update.persistence.get_repo_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args, **_kwargs: (source_file, artifact_file),
    )
    monkeypatch.setattr("lib.update.persistence.persist_materialized_updates", _persist)
    monkeypatch.setattr("lib.update.cli._get_updaters", dict)
    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_derivations",
        _unexpected_validation,
    )

    assert asyncio.run(run_updates(UpdateOptions(targets=("demo", "good")))) == 1


def test_run_updates_closes_consumer_when_phase_raises(
    monkeypatch: _MonkeyPatchLike,
    tmp_path: Path,
) -> None:
    """Unexpected phase exceptions cannot strand the UI task."""
    _use_passthrough_workspace(monkeypatch)
    flake_nix = tmp_path / "flake.nix"
    flake_lock = tmp_path / "flake.lock"
    flake_nix.write_text("before\n", encoding="utf-8")
    flake_lock.write_text("before\n", encoding="utf-8")
    consumer_closed = False

    async def _consume(
        queue: asyncio.Queue[object | None],
        *_args: object,
        **_kwargs: object,
    ) -> None:
        nonlocal consumer_closed
        while await queue.get() is not None:
            pass
        consumer_closed = True

    async def _raise(_context: object) -> UpdatePhaseResult:
        flake_lock.write_text("during phase\n", encoding="utf-8")
        raise RuntimeError("phase crashed")

    monkeypatch.setattr(
        "lib.update.cli._build_run_plan",
        lambda _opts: make_run_plan(
            source_names=("demo",),
            do_input_refresh=True,
        ),
    )
    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr("lib.update.source_runner.run_sources_phase", _raise)
    monkeypatch.setattr(
        "lib.update.persistence.get_repo_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(RuntimeError, match="phase crashed"):
        asyncio.run(run_updates(UpdateOptions(targets=("demo",))))

    assert consumer_closed


def test_run_updates_json_validation_failure_is_machine_readable(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Return one valid failure payload without human diagnostics in JSON mode."""
    _use_passthrough_workspace(monkeypatch)

    class _ValidatingUpdater(Updater):
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    def _failed_eval(
        args: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="error: package assembly is invalid",
        )

    plan = make_run_plan(source_names=("demo",))
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", drain_events)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(
            0,
            result=UpdatePhaseResult(details={"demo": "no_change"}),
        ),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "lib.update.persistence.planned_update_paths",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr("subprocess.run", _failed_eval)

    exit_code = asyncio.run(run_updates(UpdateOptions(targets=("demo",), json=True)))

    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out) == {
        "updated": [],
        "errors": ["demo"],
        "noChange": [],
        "success": False,
    }
    assert captured.err == ""


def test_emit_summary_json_outputs_payload(capsys: _CaptureLike) -> None:
    """Write summary payload to stdout in json mode."""
    summary = UpdateSummary(updated=["demo"], errors=[], no_change=["stable"])

    exit_code = _emit_summary(
        summary,
        had_errors=False,
        out=OutputOptions(json_output=True, quiet=True),
        dry_run=False,
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "updated": ["demo"],
        "errors": [],
        "noChange": ["stable"],
        "success": True,
    }
