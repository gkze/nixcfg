"""Unit tests for source-update merge behavior in the update CLI."""

import asyncio
import json
from types import SimpleNamespace
from typing import Protocol

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check
from lib.update.cli import (
    OutputOptions,
    UpdateOptions,
    UpdateSummary,
    _emit_summary,
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
    check(
        values_by_platform
        == {
            "aarch64-darwin": "sha256-DJUI4pMZ7wQTnyOiuDHALmZz7FZtrTbzRzCuNOShmWE=",
            "x86_64-linux": "sha256-cvRBvHRuunNjF07c4GVHl5rRgoTn1qfI/HdJWtOV63M=",
        }
    )


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

    check(merged is updates)


def test_run_updates_list_json_outputs_sources_and_inputs(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit machine-readable payload for list mode in json mode."""
    monkeypatch.setattr(
        "lib.update.cli._collect_flake_inputs_for_list",
        lambda: [
            SimpleNamespace(
                name="tool",
                item_type="flake",
                source="github:owner/repo",
                ref="v1.2.3",
                rev="abc123",
            ),
        ],
    )
    monkeypatch.setattr(
        "lib.update.cli._collect_source_entries_for_list",
        lambda: [
            SimpleNamespace(
                name="alpha",
                item_type="sources.json",
                source="https://example.com/alpha.tar.gz",
                ref="1.0.0",
                rev=None,
            )
        ],
    )

    opts = UpdateOptions(list_targets=True, json=True)
    exit_code = asyncio.run(run_updates(opts))

    check(exit_code == 0)
    payload = json.loads(capsys.readouterr().out)
    check(
        payload
        == {
            "rows": [
                {
                    "name": "alpha",
                    "type": "sources.json",
                    "source": "https://example.com/alpha.tar.gz",
                    "ref": "1.0.0",
                    "rev": None,
                },
                {
                    "name": "tool",
                    "type": "flake",
                    "source": "github:owner/repo",
                    "ref": "v1.2.3",
                    "rev": "abc123",
                },
            ],
        }
    )


def test_run_updates_schema_outputs_json(capsys: _CaptureLike) -> None:
    """Emit sources.json JSON schema and succeed."""
    opts = UpdateOptions(schema=True)
    exit_code = asyncio.run(run_updates(opts))

    check(exit_code == 0)
    payload = json.loads(capsys.readouterr().out)
    check(payload["type"] == "object")
    check(payload["additionalProperties"] == {"$ref": "#/$defs/SourceEntry"})


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

    check(exit_code == 0)
    payload = json.loads(capsys.readouterr().out)
    check(payload == {"valid": True, "sources": 0})


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

    check(exit_code == 1)
    payload = json.loads(capsys.readouterr().out)
    check(payload["valid"] is False)
    check("bad metadata" in payload["error"])


def test_emit_summary_json_outputs_payload(capsys: _CaptureLike) -> None:
    """Write summary payload to stdout in json mode."""
    summary = UpdateSummary(updated=["demo"], errors=[], no_change=["stable"])

    exit_code = _emit_summary(
        summary,
        had_errors=False,
        out=OutputOptions(json_output=True, quiet=True),
        dry_run=False,
    )

    check(exit_code == 0)
    payload = json.loads(capsys.readouterr().out)
    check(
        payload
        == {
            "updated": ["demo"],
            "errors": [],
            "noChange": ["stable"],
            "success": True,
        }
    )
