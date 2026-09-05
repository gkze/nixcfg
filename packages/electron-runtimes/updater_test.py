"""Focused tests for the updater-owned Electron runtime inventory."""

import json
from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal, parse_nix_expr
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind, expect_artifact_updates

_BINARY_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_REFRESHED_BINARY_HASH = "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE="
_HEADER_HASHES = (
    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
    "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
)
_CONSUMER_NAMES = ("first-consumer", "second-consumer")


def _load_module(name: str = "electron_runtimes_updater_test") -> ModuleType:
    return load_repo_module("packages/electron-runtimes/updater.py", name)


def _runtime_hash(
    module: ModuleType,
    version: str,
    artifact: str,
    hash_value: str = _BINARY_HASH,
) -> HashEntry:
    updater = module.ElectronRuntimesUpdater
    return HashEntry.create(
        "sha256",
        hash_value,
        platform=updater._runtime_key(version, artifact),
    )


def _runtime_urls(module: ModuleType, *versions: str) -> dict[str, str]:
    return module.ElectronRuntimesUpdater._required_urls(versions)


def _consumer_sources(
    *,
    version: str = "42.0.1",
) -> dict[str, SourceEntry]:
    return {
        name: SourceEntry(
            hashes={},
            electron_version=version,
        )
        for name in _CONSUMER_NAMES
    }


def _install_consumer_registry(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumers = {
        name: type(
            f"{name.title()}Updater",
            (),
            {"aggregate_into": (module.ElectronRuntimesUpdater.name,)},
        )
        for name in _CONSUMER_NAMES
    }
    monkeypatch.setattr(
        module,
        "ensure_updaters_loaded",
        lambda: {
            **consumers,
            module.ElectronRuntimesUpdater.name: module.ElectronRuntimesUpdater,
        },
    )


def test_fetch_latest_derives_versions_from_effective_consumer_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derive a unique ordered inventory from same-run consumer metadata."""
    module = _load_module("electron_runtimes_updater_policy_test")
    _install_consumer_registry(module, monkeypatch)
    sources = _consumer_sources()
    sources[_CONSUMER_NAMES[0]] = SourceEntry(hashes={}, electron_version="40.1.0")

    info = _run(
        module.ElectronRuntimesUpdater().fetch_latest(
            object(),
            context=module.UpdateContext(
                current=None,
                effective_sources=sources,
            ),
        )
    )

    assert info.version == "inventory-v1"
    assert info.metadata == module.ElectronInventoryMetadata(
        versions=("40.1.0", "42.0.1")
    )
    assert info.metadata.to_dict() == {"versions": ["40.1.0", "42.0.1"]}
    assert module.ElectronRuntimesUpdater.materialize_when_current is True
    assert module.ElectronRuntimesUpdater.generated_artifact_files == ("versions.json",)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("missing-source", "missing source metadata"),
        ("missing-version", "does not contribute"),
        ("legacy-version", "legacy pins.electronVersion"),
        ("non-exact-version", "exact semver"),
    ],
)
def test_fetch_latest_rejects_incomplete_consumer_metadata(
    mutation: str,
    match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed if any declared consumer lacks one unambiguous exact version."""
    module = _load_module(f"electron_runtimes_updater_bad_consumer_{mutation}")
    _install_consumer_registry(module, monkeypatch)
    sources = _consumer_sources()
    consumer = _CONSUMER_NAMES[0]
    if mutation == "missing-source":
        del sources[consumer]
    elif mutation == "missing-version":
        sources[consumer] = SourceEntry(hashes={})
    elif mutation == "legacy-version":
        sources[consumer] = SourceEntry(
            hashes={},
            pins={"electronVersion": "42.0.1"},
        )
    else:
        sources[consumer] = SourceEntry(hashes={}, electron_version="latest")

    with pytest.raises(RuntimeError, match=match):
        _run(
            module.ElectronRuntimesUpdater().fetch_latest(
                object(),
                context=module.UpdateContext(
                    current=None,
                    effective_sources=sources,
                ),
            )
        )


def test_fetch_latest_requires_registered_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed if updater discovery exposes no runtime contributors."""
    module = _load_module("electron_runtimes_updater_no_consumers_test")
    monkeypatch.setattr(module, "ensure_updaters_loaded", dict)

    with pytest.raises(RuntimeError, match="no registered consumers"):
        _run(
            module.ElectronRuntimesUpdater().fetch_latest(
                object(),
                context=module.UpdateContext(current=None, effective_sources={}),
            )
        )


def test_fetch_hashes_requires_a_discovered_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed if repository package discovery cannot place the projection."""
    module = _load_module("electron_runtimes_updater_missing_dir_test")
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: None)
    updater = module.ElectronRuntimesUpdater()
    hashes = [
        _runtime_hash(module, "42.0.1", artifact)
        for artifact in ("headers", *updater.PLATFORMS)
    ]

    with pytest.raises(RuntimeError, match="Package directory not found"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    module.VersionInfo(
                        version="inventory-v1",
                        metadata=module.ElectronInventoryMetadata(versions=("42.0.1",)),
                    ),
                    object(),
                    context=SourceEntry(
                        version="inventory-v1",
                        hashes=hashes,
                        urls=_runtime_urls(module, "42.0.1"),
                    ),
                )
            )
        )


def test_fetch_hashes_refreshes_every_binary_and_unpacked_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash every exported-system zip and unpacked headers per exact version."""
    module = _load_module("electron_runtimes_updater_hash_test")
    updater = module.ElectronRuntimesUpdater()
    binary_urls: list[str] = []
    header_exprs: list[str] = []

    async def _url_hashes(name: str, urls, *, config=None):
        assert name == updater.name
        assert config == updater.config
        binary_urls.extend(urls)
        yield UpdateEvent.status(name, "hashing Electron binaries")
        yield UpdateEvent.value(name, dict.fromkeys(binary_urls, _BINARY_HASH))

    async def _fixed_hash(name: str, expr: str, *, config=None):
        assert name == updater.name
        assert config == updater.config
        header_exprs.append(expr)
        yield UpdateEvent.status(name, "hashing Electron headers")
        yield UpdateEvent.value(name, _HEADER_HASHES[len(header_exprs) - 1])

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _url_hashes)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                module.VersionInfo(
                    version="inventory-v1",
                    metadata={"versions": ["40.1.0", "42.0.1"]},
                ),
                object(),
            )
        )
    )

    assert len(binary_urls) == 2 * len(updater.PLATFORMS)
    assert binary_urls[0].endswith("/electron-v40.1.0-darwin-arm64.zip")
    assert binary_urls[-1].endswith("/electron-v42.0.1-linux-x64.zip")
    assert len(header_exprs) == 2
    assert_nix_ast_equal(
        parse_nix_expr(header_exprs[0]),
        """
        pkgs.fetchzip {
          name = "electron-40.1.0-headers";
          url = "https://artifacts.electronjs.org/headers/dist/v40.1.0/node-v40.1.0-headers.tar.gz";
          hash = pkgs.lib.fakeHash;
        }
        """,
    )
    assert next(event.kind for event in events) is UpdateEventKind.STATUS
    artifact_event = next(
        event for event in events if event.kind is UpdateEventKind.ARTIFACT
    )
    artifact = expect_artifact_updates(artifact_event.payload)[0]
    assert artifact.path.name == "versions.json"
    assert json.loads(artifact.content) == {
        "schemaVersion": 1,
        "versions": ["40.1.0", "42.0.1"],
    }
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload == [
        _runtime_hash(
            module,
            "40.1.0",
            "headers",
            _HEADER_HASHES[0],
        ),
        *[_runtime_hash(module, "40.1.0", platform) for platform in updater.PLATFORMS],
        _runtime_hash(
            module,
            "42.0.1",
            "headers",
            _HEADER_HASHES[1],
        ),
        *[_runtime_hash(module, "42.0.1", platform) for platform in updater.PLATFORMS],
    ]


@pytest.mark.parametrize(
    "hash_entry",
    [
        HashEntry.create(
            "yarnRootHash",
            _BINARY_HASH,
            platform="42.0.1:headers",
        ),
        HashEntry.create("sha256", _BINARY_HASH),
    ],
)
def test_fetch_hashes_rejects_malformed_current_records(hash_entry: HashEntry) -> None:
    """Do not silently reuse records outside the strict inventory key schema."""
    module = _load_module("electron_runtimes_updater_malformed_record_test")
    current = SourceEntry(version="inventory-v1", hashes=[hash_entry])

    with pytest.raises(RuntimeError, match="malformed hash record"):
        module.ElectronRuntimesUpdater._current_hashes(current)


def test_fetch_hashes_rejects_duplicate_current_records() -> None:
    """Reject ambiguous duplicate artifact records before deciding what to reuse."""
    module = _load_module("electron_runtimes_updater_duplicate_record_test")
    entry = _runtime_hash(module, "42.0.1", "headers")
    current = SourceEntry(version="inventory-v1", hashes=[entry, entry])

    with pytest.raises(RuntimeError, match="duplicate record"):
        module.ElectronRuntimesUpdater._current_hashes(current)


def test_fetch_hashes_requires_complete_version_metadata() -> None:
    """Reject calls that bypass the validated exact-version policy."""
    module = _load_module("electron_runtimes_updater_metadata_test")
    updater = module.ElectronRuntimesUpdater()

    with pytest.raises(TypeError, match="version list"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    module.VersionInfo(version="inventory-v1", metadata={}),
                    object(),
                )
            )
        )


@pytest.mark.parametrize(
    ("raw_versions", "error_type", "match"),
    [
        ("42.0.1", TypeError, "must be an array"),
        ([], RuntimeError, "at least one Electron version"),
        (["42.0.1", 42], TypeError, "contain only strings"),
        (
            ["42.0.1", "40.1.0"],
            RuntimeError,
            "unique and strictly increasing",
        ),
        (
            ["42.0.1", "42.0.1"],
            RuntimeError,
            "unique and strictly increasing",
        ),
    ],
)
def test_runtime_inventory_rejects_invalid_version_lists(
    raw_versions: object,
    error_type: type[Exception],
    match: str,
) -> None:
    """Reject malformed, empty, unordered, and duplicate runtime policies."""
    module = _load_module("electron_runtimes_updater_invalid_versions_test")

    with pytest.raises(error_type, match=match):
        module._validate_versions(raw_versions)


def test_latest_check_requires_the_complete_non_fake_inventory() -> None:
    """Only skip hashing when every policy artifact has a real persisted hash."""
    module = _load_module("electron_runtimes_updater_latest_test")
    updater = module.ElectronRuntimesUpdater()
    info = module.VersionInfo(
        version="inventory-v1",
        metadata={"versions": ["42.0.1"]},
    )
    complete = SourceEntry(
        version="inventory-v1",
        hashes=[
            _runtime_hash(module, "42.0.1", artifact)
            for artifact in ("headers", *updater.PLATFORMS)
        ],
        urls=_runtime_urls(module, "42.0.1"),
    )

    assert _run(updater._is_latest(complete, info)) is True
    assert _run(updater._is_latest(None, info)) is False
    assert (
        _run(
            updater._is_latest(
                complete.model_copy(update={"version": "inventory-v0"}),
                info,
            )
        )
        is False
    )
    assert (
        _run(
            updater._is_latest(
                complete.model_copy(
                    update={
                        "hashes": complete.hashes.model_copy(
                            update={"entries": complete.hashes.entries[:-1]}
                        )
                    }
                ),
                info,
            )
        )
        is False
    )
    fake = complete.model_copy(deep=True)
    assert fake.hashes.entries is not None
    fake.hashes.entries[0] = fake.hashes.entries[0].model_copy(
        update={"hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    )
    assert _run(updater._is_latest(fake, info)) is False


def test_artifact_tag_drift_invalidates_and_rehashes_the_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tie hash reuse to the exact policy-selected upstream artifact URL."""
    module = _load_module("electron_runtimes_updater_artifact_tag_drift_test")
    updater = module.ElectronRuntimesUpdater()
    version = "42.0.1"
    hashes = [
        _runtime_hash(module, version, artifact)
        for artifact in ("headers", *updater.PLATFORMS)
    ]
    current = SourceEntry(
        version="inventory-v1",
        hashes=hashes,
        urls=_runtime_urls(module, version),
    )
    info = module.VersionInfo(
        version="inventory-v1",
        metadata={"versions": [version]},
    )
    requested_urls: list[str] = []

    monkeypatch.setitem(updater.PLATFORMS, "aarch64-darwin", "changed-tag")

    async def _url_hashes(name: str, urls, *, config=None):
        assert name == updater.name
        assert config == updater.config
        requested_urls.extend(urls)
        yield UpdateEvent.value(
            name,
            dict.fromkeys(requested_urls, _REFRESHED_BINARY_HASH),
        )

    def _unexpected_header_hash(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unchanged Electron headers unexpectedly triggered hashing")

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _url_hashes)
    monkeypatch.setattr(
        "lib.update.nix.compute_fixed_output_hash",
        _unexpected_header_hash,
    )

    assert _run(updater._is_latest(current, info)) is False
    events = _run(
        _collect_events(
            updater.fetch_hashes(
                info,
                object(),
                context=current,
            )
        )
    )

    expected_url = (
        "https://github.com/electron/electron/releases/download/"
        f"v{version}/electron-v{version}-changed-tag.zip"
    )
    assert requested_urls == [expected_url]
    result = events[-1].payload
    assert isinstance(result, list)
    refreshed = next(
        entry for entry in result if entry.platform == f"{version}:aarch64-darwin"
    )
    assert refreshed.hash == _REFRESHED_BINARY_HASH
    refreshed_source = updater.build_result(info, result)
    assert refreshed_source.urls is not None
    assert refreshed_source.urls[f"{version}:aarch64-darwin"] == expected_url
    merged = current.merge_native_update(refreshed_source)
    assert merged.urls == refreshed_source.urls
    assert merged.hashes.entries is not None
    assert len(merged.hashes.entries) == len(hashes)


def test_fetch_hashes_reuses_a_complete_current_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid re-downloading artifacts when every exact policy record is reusable."""
    module = _load_module("electron_runtimes_updater_reuse_test")
    updater = module.ElectronRuntimesUpdater()
    hashes = [
        _runtime_hash(module, "42.0.1", artifact)
        for artifact in ("headers", *updater.PLATFORMS)
    ]
    current = SourceEntry(
        version="inventory-v1",
        hashes=hashes,
        urls=_runtime_urls(module, "42.0.1"),
    )

    def _unexpected_hash(*_args: object, **_kwargs: object) -> None:
        pytest.fail("complete inventory unexpectedly triggered artifact hashing")

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _unexpected_hash)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _unexpected_hash)

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                module.VersionInfo(
                    version="inventory-v1",
                    metadata={"versions": ["42.0.1"]},
                ),
                object(),
                context=current,
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.VALUE,
    ]
    artifact = expect_artifact_updates(events[0].payload)[0]
    assert json.loads(artifact.content) == {
        "schemaVersion": 1,
        "versions": ["42.0.1"],
    }
    assert events[1] == UpdateEvent.value(updater.name, hashes)


def test_fetch_hashes_requires_every_binary_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the bulk hasher omits one requested platform URL."""
    module = _load_module("electron_runtimes_updater_missing_binary_test")
    updater = module.ElectronRuntimesUpdater()

    async def _url_hashes(name: str, urls, *, config=None):
        _ = config
        requested = list(urls)
        yield UpdateEvent.value(name, dict.fromkeys(requested[:-1], _BINARY_HASH))

    async def _fixed_hash(name: str, _expr: str, *, config=None):
        _ = config
        yield UpdateEvent.value(name, _HEADER_HASHES[0])

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _url_hashes)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    with pytest.raises(RuntimeError, match="Missing Electron binary hash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    module.VersionInfo(
                        version="inventory-v1",
                        metadata={"versions": ["42.0.1"]},
                    ),
                    object(),
                )
            )
        )


def test_build_result_persists_updater_owned_artifact_identity() -> None:
    """Persist the exact URL beside each hash without derivation-side identity."""
    module = _load_module("electron_runtimes_updater_result_test")
    updater = module.ElectronRuntimesUpdater()
    hashes = [_runtime_hash(module, "42.0.1", "aarch64-darwin")]

    result = updater.build_result(
        module.VersionInfo(
            version="inventory-v1",
            metadata={"versions": ["42.0.1"]},
        ),
        hashes,
    )

    assert result == SourceEntry(
        version="inventory-v1",
        hashes=hashes,
        urls=_runtime_urls(module, "42.0.1"),
    )
