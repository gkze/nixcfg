"""Tests for per-package sources.json loading helpers."""

import json
from pathlib import Path

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.update.persistence import persist_source_updates
from lib.update.sources import load_all_sources, load_source_entry
from lib.update.sources import save_sources as save_all_sources


def test_load_source_entry_accepts_object_payload(tmp_path: Path) -> None:
    """Per-package object payloads load as SourceEntry."""
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "hashes": [
                    {
                        "hashType": "denoDepsHash",
                        "hash": "sha256-ubFvvC6Nw+4TNSmPe2NFZkxm7TiqnOX9+c4FyasrL5U=",
                        "platform": "aarch64-darwin",
                    },
                ],
                "input": "linear-cli",
            },
        ),
    )

    entry = load_source_entry(path)

    assert entry.input == "linear-cli"
    entries = entry.hashes.entries
    if entries is None:
        raise AssertionError
    assert entries[0].platform == "aarch64-darwin"


def test_load_source_entry_accepts_legacy_list_payload(tmp_path: Path) -> None:
    """Legacy list payloads are treated as hashes for compatibility."""
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            [
                {
                    "hashType": "denoDepsHash",
                    "hash": "sha256-VBJl6rFuwI7UMkyTLYdYJ+cYjm6thTDsHAxfVuzvTxc=",
                    "platform": "x86_64-linux",
                },
            ],
        ),
    )

    entry = load_source_entry(path)

    entries = entry.hashes.entries
    if entries is None:
        raise AssertionError
    assert len(entries) == 1
    assert entries[0].platform == "x86_64-linux"


def test_save_sources_raises_for_unknown_source_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when a source has no mapped per-package destination."""
    monkeypatch.setattr("lib.update.sources._source_file_map", dict)

    entry = SourceEntry(
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    hash_type="sha256",
                    hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                ),
            ],
        ),
    )
    sources = SourcesFile(entries={"demo": entry})

    with pytest.raises(RuntimeError, match="demo"):
        save_all_sources(sources)


def test_save_sources_writes_entry_to_mapped_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write entries atomically when a mapping exists."""
    dest = tmp_path / "packages" / "demo" / "sources.json"
    dest.parent.mkdir(parents=True)
    dest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: {"demo": Path(dest)},
    )

    entry = SourceEntry(
        hashes=HashCollection(
            entries=[
                HashEntry.create(
                    hash_type="sha256",
                    hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                ),
            ],
        ),
        version="1.2.3",
    )

    save_all_sources(SourcesFile(entries={"demo": entry}))

    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["version"] == "1.2.3"
    assert saved["hashes"][0]["hashType"] == "sha256"


def test_persist_source_updates_writes_only_successful_run_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve newer disk state for failed and unrelated source entries."""
    source_paths = {
        name: tmp_path / "packages" / name / "sources.json"
        for name in ("updated", "failed", "unrelated")
    }
    for path in source_paths.values():
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"hashes": {}, "version": "1.0.0"}),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: source_paths,
    )
    run_snapshot = load_all_sources()

    source_paths["failed"].write_text(
        json.dumps({"hashes": {}, "version": "1.5.0"}),
        encoding="utf-8",
    )
    source_paths["unrelated"].write_text(
        json.dumps({"hashes": {}, "version": "9.0.0"}),
        encoding="utf-8",
    )

    persist_source_updates(
        do_sources=True,
        source_names=["updated", "failed"],
        dry_run=False,
        native_only=False,
        sources=run_snapshot,
        source_updates={
            "updated": SourceEntry(hashes={}, version="2.0.0"),
            "failed": SourceEntry(hashes={}, version="2.0.0"),
        },
        details={"updated": "updated", "failed": "error"},
    )

    persisted_versions = {
        name: json.loads(path.read_text(encoding="utf-8"))["version"]
        for name, path in source_paths.items()
    }
    assert persisted_versions == {
        "updated": "2.0.0",
        "failed": "1.5.0",
        "unrelated": "9.0.0",
    }


def test_persist_source_updates_preserves_concurrent_native_platform_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge each native-only update with disk state under the source lock."""
    source_path = tmp_path / "packages" / "demo" / "sources.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({
            "hashes": {
                "aarch64-darwin": "sha256-oldDarwin",
                "x86_64-linux": "sha256-oldLinux",
            },
            "version": "1.0.0",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: {"demo": source_path},
    )

    darwin_snapshot = load_all_sources()
    linux_snapshot = load_all_sources()

    persist_source_updates(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        native_only=True,
        sources=darwin_snapshot,
        source_updates={
            "demo": SourceEntry(
                hashes={"aarch64-darwin": "sha256-newDarwin"},
                version="1.1.0",
            )
        },
        details={"demo": "updated"},
    )
    persist_source_updates(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        native_only=True,
        sources=linux_snapshot,
        source_updates={
            "demo": SourceEntry(
                hashes={"x86_64-linux": "sha256-newLinux"},
                version="1.1.0",
            )
        },
        details={"demo": "updated"},
    )

    persisted = load_source_entry(source_path)
    assert persisted.hashes.mapping == {
        "aarch64-darwin": "sha256-newDarwin",
        "x86_64-linux": "sha256-newLinux",
    }
