"""Tests for merge-sources CI helper behavior."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lib.update.ci.merge_sources import main

if TYPE_CHECKING:
    from pathlib import Path


def _source_entry_payload(
    *, version: str, hashes: list[dict[str, str]]
) -> dict[str, object]:
    return {
        "version": version,
        "hashes": hashes,
    }


def _write_source_entry(path: Path, payload: dict[str, object] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        payload = _source_entry_payload(
            version="1.0.0",
            hashes=[
                {
                    "hashType": "sha256",
                    "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                },
            ],
        )
    path.write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def test_merge_sources_fails_for_missing_and_empty_roots(tmp_path: Path) -> None:
    """Reject merge inputs when any requested root is missing or empty."""
    valid_root = tmp_path / "sources-x86_64-linux"
    empty_root = tmp_path / "sources-aarch64-linux"
    missing_root = tmp_path / "sources-aarch64-darwin"
    output_root = tmp_path / "repo"

    _write_source_entry(valid_root / "packages" / "demo" / "sources.json")
    empty_root.mkdir(parents=True)
    _write_source_entry(output_root / "packages" / "demo" / "sources.json")

    with pytest.raises(RuntimeError, match="Invalid merge input roots") as exc:
        main(
            [
                str(valid_root),
                str(empty_root),
                str(missing_root),
                "--output-root",
                str(output_root),
            ],
        )

    message = str(exc.value)
    assert str(missing_root) in message  # noqa: S101
    assert str(empty_root) in message  # noqa: S101


def test_merge_sources_keeps_platform_hashes_from_matching_roots(
    tmp_path: Path,
) -> None:
    """Only the matching platform hash should be taken from each root."""
    darwin_root = tmp_path / "sources-aarch64-darwin"
    linux_root = tmp_path / "sources-x86_64-linux"
    arm_linux_root = tmp_path / "sources-aarch64-linux"
    output_root = tmp_path / "repo"

    stale_darwin = "sha256-b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b="
    stale_linux = "sha256-c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c="
    stale_arm_linux = "sha256-d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d="

    fresh_darwin = "sha256-e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e="
    fresh_linux = "sha256-f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f5f="
    fresh_arm_linux = "sha256-g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g6g="

    _write_source_entry(
        darwin_root / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.0.1",
            hashes=[
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-darwin",
                    "hash": fresh_darwin,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "x86_64-linux",
                    "hash": stale_linux,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-linux",
                    "hash": stale_arm_linux,
                },
            ],
        ),
    )
    _write_source_entry(
        linux_root / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.0.1",
            hashes=[
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-darwin",
                    "hash": stale_darwin,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "x86_64-linux",
                    "hash": fresh_linux,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-linux",
                    "hash": stale_arm_linux,
                },
            ],
        ),
    )
    _write_source_entry(
        arm_linux_root / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.0.1",
            hashes=[
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-darwin",
                    "hash": stale_darwin,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "x86_64-linux",
                    "hash": stale_linux,
                },
                {
                    "hashType": "denoDepsHash",
                    "platform": "aarch64-linux",
                    "hash": fresh_arm_linux,
                },
            ],
        ),
    )
    _write_source_entry(
        output_root / "packages" / "demo" / "sources.json",
    )

    assert (  # noqa: S101
        main(
            [
                str(darwin_root),
                str(linux_root),
                str(arm_linux_root),
                "--output-root",
                str(output_root),
            ],
        )
        == 0
    )

    merged_path = output_root / "packages" / "demo" / "sources.json"
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    by_platform = {
        entry["platform"]: entry["hash"]
        for entry in merged["hashes"]
        if entry["hashType"] == "denoDepsHash"
    }
    assert by_platform["aarch64-darwin"] == fresh_darwin  # noqa: S101
    assert by_platform["x86_64-linux"] == fresh_linux  # noqa: S101
    assert by_platform["aarch64-linux"] == fresh_arm_linux  # noqa: S101


def test_merge_sources_creates_missing_output_destination(tmp_path: Path) -> None:
    """Write merged entry when the package dir exists but sources.json does not."""
    linux_root = tmp_path / "sources-x86_64-linux"
    output_root = tmp_path / "repo"

    _write_source_entry(
        linux_root / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.2.3",
            hashes=[
                {
                    "hashType": "sha256",
                    "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                },
            ],
        ),
    )

    # Output repo has the package directory but no sources.json yet.
    (output_root / "packages" / "demo").mkdir(parents=True, exist_ok=True)

    assert main([str(linux_root), "--output-root", str(output_root)]) == 0  # noqa: S101

    merged_path = output_root / "packages" / "demo" / "sources.json"
    assert merged_path.is_file()  # noqa: S101
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert merged["version"] == "1.2.3"  # noqa: S101


def test_merge_sources_rejects_conflicting_non_platform_hashes(tmp_path: Path) -> None:
    """Reject roots that disagree on non-platform hash values."""
    root_a = tmp_path / "sources-aarch64-darwin"
    root_b = tmp_path / "sources-x86_64-linux"
    output_root = tmp_path / "repo"

    _write_source_entry(
        root_a / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.0.0",
            hashes=[
                {
                    "hashType": "sha256",
                    "hash": "sha256-h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h7h=",
                },
            ],
        ),
    )
    _write_source_entry(
        root_b / "packages" / "demo" / "sources.json",
        _source_entry_payload(
            version="1.0.0",
            hashes=[
                {
                    "hashType": "sha256",
                    "hash": "sha256-i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i8i=",
                },
            ],
        ),
    )
    _write_source_entry(output_root / "packages" / "demo" / "sources.json")

    with pytest.raises(RuntimeError, match="Conflicting non-platform hash entry"):
        main(
            [
                str(root_a),
                str(root_b),
                "--output-root",
                str(output_root),
            ],
        )
