"""Tests for per-package sources.json loading helpers."""

import json
from pathlib import Path

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.update.sources import load_source_entry
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

    assert entry.input == "linear-cli"  # noqa: S101
    assert entry.hashes.entries is not None  # noqa: S101
    assert entry.hashes.entries[0].platform == "aarch64-darwin"  # noqa: S101


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

    assert entry.hashes.entries is not None  # noqa: S101
    assert len(entry.hashes.entries) == 1  # noqa: S101
    assert entry.hashes.entries[0].platform == "x86_64-linux"  # noqa: S101


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
    assert saved["version"] == "1.2.3"  # noqa: S101
    assert saved["hashes"][0]["hashType"] == "sha256"  # noqa: S101
