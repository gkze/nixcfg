"""Tests for per-package sources.json loading helpers."""

import json
from pathlib import Path

import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourcesFile
from lib.update.persistence import persist_source_updates
from lib.update.sources import (
    load_all_sources,
    load_source_entry,
    read_pinned_source_version,
    save_source_updates,
)
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


def test_read_pinned_source_version_requires_source_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail clearly when no updater-managed sources file owns the package."""
    monkeypatch.setattr("lib.update.sources.sources_file_for", lambda _name: None)

    with pytest.raises(RuntimeError, match="sources.json not found for demo"):
        read_pinned_source_version("demo")


@pytest.mark.parametrize("version", [None, ""])
def test_read_pinned_source_version_requires_nonempty_version(
    version: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject source sidecars that cannot identify the pinned release."""
    source_path = tmp_path / "sources.json"
    source_path.write_text(
        json.dumps({"hashes": {}, "version": version}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lib.update.sources.sources_file_for",
        lambda _name: source_path,
    )

    with pytest.raises(
        RuntimeError,
        match="demo sources.json is missing a pinned version",
    ):
        read_pinned_source_version("demo")


def test_source_entry_preserves_and_updates_electron_version() -> None:
    """Round-trip strict Electron metadata and merge newer values when supplied."""
    original = SourceEntry.model_validate({
        "hashes": {},
        "electronVersion": "41.7.0",
        "version": "0.38.0",
    })

    assert original.electron_version == "41.7.0"
    assert original.to_dict()["electronVersion"] == "41.7.0"
    assert original.merge(SourceEntry(hashes={})).electron_version == "41.7.0"
    assert (
        original.merge(
            SourceEntry(hashes={}, electron_version="42.0.0"),
        ).electron_version
        == "42.0.0"
    )


def test_save_source_updates_generic_merge_preserves_pin_union(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the legacy pin-union behavior for non-native merge callers."""
    source_path = tmp_path / "sources.json"
    source_path.write_text(
        json.dumps({
            "hashes": {},
            "pins": {"existing": "kept", "updated": "old"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: {"demo": source_path},
    )

    persisted = save_source_updates(
        {
            "demo": SourceEntry(
                hashes={},
                pins={"added": "new", "updated": "new"},
            )
        },
        merge_existing=True,
    )

    assert persisted["demo"].pins == {
        "added": "new",
        "existing": "kept",
        "updated": "new",
    }
    assert load_source_entry(source_path).pins == persisted["demo"].pins


def test_source_reads_and_new_writes_follow_authoritative_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore shadow directories when source and updater sidecars identify owners."""
    source_dir = tmp_path / "packages" / "demo"
    updater_dir = tmp_path / "overlays" / "new-demo"
    for path in (
        source_dir,
        tmp_path / "overlays" / "demo",
        updater_dir,
        tmp_path / "packages" / "new-demo",
    ):
        path.mkdir(parents=True)
    (source_dir / "sources.json").write_text(
        json.dumps({"hashes": {}, "version": "1.2.3"}),
        encoding="utf-8",
    )
    (updater_dir / "updater.py").write_text("# updater\n", encoding="utf-8")
    flat_updater = tmp_path / "overlays" / "flat-demo.updater.py"
    flat_updater.write_text("# updater\n", encoding="utf-8")
    monkeypatch.setattr("lib.update.paths.get_repo_root", lambda: tmp_path)

    assert read_pinned_source_version("demo") == "1.2.3"

    save_source_updates(
        {
            "new-demo": SourceEntry(hashes={}, version="2.0.0"),
            "flat-demo": SourceEntry(hashes={}, version="3.0.0"),
        },
    )
    created = json.loads(
        (updater_dir / "sources.json").read_text(encoding="utf-8"),
    )
    assert created["version"] == "2.0.0"
    flat_created = json.loads(
        (tmp_path / "overlays" / "flat-demo.sources.json").read_text(
            encoding="utf-8",
        ),
    )
    assert flat_created["version"] == "3.0.0"
    assert not (updater_dir / "sources.json.lock").exists()
    assert not (tmp_path / "overlays" / "flat-demo.sources.json.lock").exists()


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
            "pins": {"runtimeVersion": "1.1.0"},
            "version": "1.1.0",
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
                pins={"runtimeVersion": "1.1.0"},
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
                pins={"runtimeVersion": "1.1.0"},
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
    assert persisted.pins == {"runtimeVersion": "1.1.0"}


def test_persist_source_updates_replaces_single_platform_pins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locked native persistence deletes pins after a complete hash refresh."""
    source_path = tmp_path / "packages" / "demo" / "sources.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps({
            "hashes": {"aarch64-darwin": "sha256-oldDarwin"},
            "pins": {"removed": "obsolete", "runtimeVersion": "1.0.0"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lib.update.sources._source_file_map",
        lambda: {"demo": source_path},
    )
    sources = load_all_sources()

    persist_source_updates(
        do_sources=True,
        source_names=["demo"],
        dry_run=False,
        native_only=True,
        sources=sources,
        source_updates={
            "demo": SourceEntry(
                hashes={"aarch64-darwin": "sha256-newDarwin"},
                pins={"runtimeVersion": "2.0.0"},
            )
        },
        details={"demo": "updated"},
    )

    persisted = load_source_entry(source_path)
    assert persisted.hashes.mapping == {"aarch64-darwin": "sha256-newDarwin"}
    assert persisted.pins == {"runtimeVersion": "2.0.0"}
