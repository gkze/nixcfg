"""Unit tests for source-update merge behavior in the update CLI."""

import asyncio
import json
from types import SimpleNamespace
from typing import Protocol

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.cli import (
    OutputOptions,
    UpdateOptions,
    UpdateSummary,
    _emit_summary,
    _InventoryHandles,
    _InventoryRefTarget,
    _InventorySourceTarget,
    _InventoryTarget,
    _merge_source_updates,
    run_updates,
)


class _MonkeyPatchLike(Protocol):
    def setattr(self, target: str, value: object) -> None: ...


class _CapturedOut(Protocol):
    out: str


class _CaptureLike(Protocol):
    def readouterr(self) -> _CapturedOut: ...


def _entry_with_hashes(*entries: HashEntry) -> SourceEntry:
    return SourceEntry(hashes=HashCollection(entries=list(entries)))


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

    merged = _merge_source_updates(existing, updates, native_only=True)

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

    merged = _merge_source_updates({}, updates, native_only=False)

    assert merged is updates


def test_run_updates_list_json_outputs_sources_and_inputs(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit machine-readable inventory payload for list mode."""
    monkeypatch.setattr(
        "lib.update.cli._build_update_inventory",
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
        "lib.update.cli.load_all_sources",
        lambda: SimpleNamespace(entries={}),
    )
    monkeypatch.setattr(
        "lib.update.cli.validate_source_discovery_consistency",
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
        "lib.update.cli.load_all_sources",
        lambda: SimpleNamespace(entries={}),
    )

    def _boom() -> None:
        msg = "bad metadata"
        raise ValueError(msg)

    monkeypatch.setattr("lib.update.cli.validate_source_discovery_consistency", _boom)

    opts = UpdateOptions(validate=True, json=True)
    exit_code = asyncio.run(run_updates(opts))

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert "bad metadata" in payload["error"]


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
