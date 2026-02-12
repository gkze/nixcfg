"""Unit tests for source-update merge behavior in the update CLI."""

import argparse
import asyncio
import json
from types import SimpleNamespace
from typing import Protocol

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.cli import (
    OutputOptions,
    UpdateSummary,
    _emit_summary,
    _merge_source_updates,
)
from lib.update.cli import _run_updates as run_updates


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
    assert result_entries is not None  # noqa: S101
    values_by_platform = {entry.platform: entry.hash for entry in result_entries}
    assert values_by_platform == {  # noqa: S101
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

    assert merged is updates  # noqa: S101


def test_run_updates_list_json_outputs_sources_and_inputs(
    monkeypatch: _MonkeyPatchLike,
    capsys: _CaptureLike,
) -> None:
    """Emit machine-readable payload for list mode in json mode."""
    monkeypatch.setattr(
        "lib.update.cli.UPDATERS",
        {"zeta": object, "alpha": object},
    )
    monkeypatch.setattr(
        "lib.update.cli.get_flake_inputs_with_refs",
        lambda: [
            SimpleNamespace(name="tool", owner="owner", repo="repo", ref="v1.2.3"),
        ],
    )

    args = argparse.Namespace(
        schema=False,
        list=True,
        validate=False,
        json=True,
        quiet=False,
    )
    exit_code = asyncio.run(run_updates(args))

    assert exit_code == 0  # noqa: S101
    payload = json.loads(capsys.readouterr().out)
    assert payload == {  # noqa: S101
        "sources": ["alpha", "zeta"],
        "inputs": [
            {
                "name": "tool",
                "owner": "owner",
                "repo": "repo",
                "ref": "v1.2.3",
            },
        ],
    }


def test_run_updates_schema_outputs_json(capsys: _CaptureLike) -> None:
    """Emit sources.json JSON schema and succeed."""
    args = argparse.Namespace(
        schema=True,
        list=False,
        validate=False,
        json=False,
        quiet=False,
    )

    exit_code = asyncio.run(run_updates(args))

    assert exit_code == 0  # noqa: S101
    payload = json.loads(capsys.readouterr().out)
    assert payload["type"] == "object"  # noqa: S101
    assert payload["additionalProperties"] == {"$ref": "#/$defs/SourceEntry"}  # noqa: S101


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

    args = argparse.Namespace(
        schema=False,
        list=False,
        validate=True,
        json=True,
        quiet=False,
    )
    exit_code = asyncio.run(run_updates(args))

    assert exit_code == 0  # noqa: S101
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"valid": True, "sources": 0}  # noqa: S101


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

    args = argparse.Namespace(
        schema=False,
        list=False,
        validate=True,
        json=True,
        quiet=False,
    )
    exit_code = asyncio.run(run_updates(args))

    assert exit_code == 1  # noqa: S101
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False  # noqa: S101
    assert "bad metadata" in payload["error"]  # noqa: S101


def test_emit_summary_json_outputs_payload(capsys: _CaptureLike) -> None:
    """Write summary payload to stdout in json mode."""
    summary = UpdateSummary(updated=["demo"], errors=[], no_change=["stable"])
    args = argparse.Namespace(json=True)

    exit_code = _emit_summary(
        args,
        summary,
        had_errors=False,
        out=OutputOptions(json_output=True, quiet=True),
        dry_run=False,
    )

    assert exit_code == 0  # noqa: S101
    payload = json.loads(capsys.readouterr().out)
    assert payload == {  # noqa: S101
        "updated": ["demo"],
        "errors": [],
        "noChange": ["stable"],
        "success": True,
    }
