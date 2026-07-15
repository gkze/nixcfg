"""Unit tests for source-update merge behavior in the update CLI."""

import asyncio
import json
import subprocess
from types import SimpleNamespace
from typing import Protocol

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    UpdateOptions,
    UpdateSummary,
    _emit_summary,
    _RunPlan,
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


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


class _CapturedOut(Protocol):
    out: str
    err: str


class _CaptureLike(Protocol):
    def readouterr(self) -> _CapturedOut: ...


def _entry_with_hashes(*entries: HashEntry) -> SourceEntry:
    return SourceEntry(hashes=HashCollection(entries=list(entries)))


def _demo_run_plan(*, dry_run: bool) -> _RunPlan:
    resolved = ResolvedTargets(
        all_source_names={"demo"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"demo"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=False,
        dry_run=dry_run,
        native_only=False,
        ref_inputs=[],
        source_names=["demo"],
    )
    return _RunPlan(
        resolved=resolved,
        tty_enabled=False,
        show_phase_headers=False,
        sources=SourcesFile(entries={"demo": SourceEntry(hashes={})}),
        item_meta={"demo": SimpleNamespace(name="demo", origin="x", op_order=())},
        order=["demo"],
    )


def test_merge_source_updates_native_only_preserves_other_platform_hashes() -> None:
    """Merge native updates while preserving non-native existing hashes."""
    existing = {
        "opencode": _entry_with_hashes(
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
        ),
    }
    updates = {
        "opencode": _entry_with_hashes(
            HashEntry.create(
                hash_type="nodeModulesHash",
                hash_value="sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
                platform="aarch64-darwin",
            ),
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

    class _ValidatingUpdater:
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    plan = _demo_run_plan(dry_run=False)
    events: list[str] = []

    async def _consume(*_args: object, **_kwargs: object) -> SimpleNamespace:
        queue = _args[0]
        while await queue.get() is not None:
            pass
        return SimpleNamespace(
            errors=0,
            details={"demo": "no_change"},
            source_updates={},
            artifact_updates={},
        )

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

    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        _persist,
    )
    monkeypatch.setattr("subprocess.run", _run_nix_eval)

    exit_code = asyncio.run(run_updates(UpdateOptions(targets=("demo",))))

    assert exit_code == 1
    assert events == ["persist", "validate"]
    captured = capsys.readouterr()
    assert "Failed: demo" in captured.err
    assert "attribute 'missing-member' missing" in captured.err


@pytest.mark.parametrize(
    ("dry_run", "update_errors", "detail", "expected_exit"),
    [
        (True, 0, "updated", 0),
        (False, 1, "error", 1),
    ],
)
def test_run_updates_skips_derivation_validation_for_incomplete_runs(
    monkeypatch: _MonkeyPatchLike,
    dry_run: bool,
    update_errors: int,
    detail: str,
    expected_exit: int,
) -> None:
    """Do not evaluate a dry-run or a tree left incomplete by update errors."""

    class _ValidatingUpdater:
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    async def _consume(*_args: object, **_kwargs: object) -> SimpleNamespace:
        queue = _args[0]
        while await queue.get() is not None:
            pass
        return SimpleNamespace(
            errors=update_errors,
            details={"demo": detail},
            source_updates={},
            artifact_updates={},
        )

    def _unexpected_eval(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("incomplete update must not evaluate derivations")

    plan = _demo_run_plan(dry_run=dry_run)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("subprocess.run", _unexpected_eval)

    assert (
        asyncio.run(run_updates(UpdateOptions(targets=("demo",), check=dry_run)))
        == expected_exit
    )


def test_run_updates_json_validation_failure_is_machine_readable(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Return one valid failure payload without human diagnostics in JSON mode."""

    class _ValidatingUpdater:
        derivation_validations = (
            DerivationValidation(installable=".#packages.demo.drvPath"),
        )

    async def _consume(*_args: object, **_kwargs: object) -> SimpleNamespace:
        queue = _args[0]
        while await queue.get() is not None:
            pass
        return SimpleNamespace(
            errors=0,
            details={"demo": "no_change"},
            source_updates={},
            artifact_updates={},
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

    plan = _demo_run_plan(dry_run=False)
    monkeypatch.setattr("lib.update.cli._build_run_plan", lambda _opts, _out: plan)
    monkeypatch.setattr(
        "lib.update.cli._get_updaters", lambda: {"demo": _ValidatingUpdater}
    )
    monkeypatch.setattr("lib.update.cli.consume_events", _consume)
    monkeypatch.setattr(
        "lib.update.source_runner.run_sources_phase",
        lambda _context: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        "lib.update.persistence.persist_materialized_updates",
        lambda **_kwargs: None,
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
