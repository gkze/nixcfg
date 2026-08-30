"""Focused tests for the updater-owned Electron runtime inventory."""

import json
from pathlib import Path
from types import ModuleType

import pytest

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal, parse_nix_expr
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind

_BINARY_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_HEADER_HASHES = (
    "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
    "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
)


def _load_module(name: str = "electron_runtimes_updater_test") -> ModuleType:
    return load_repo_module("packages/electron-runtimes/updater.py", name)


def _write_policy(path: Path, versions: list[str]) -> None:
    path.write_text(
        json.dumps({"schemaVersion": 1, "versions": versions}) + "\n",
        encoding="utf-8",
    )


def test_fetch_latest_uses_the_validated_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expose the exact ordered version policy through updater metadata."""
    module = _load_module("electron_runtimes_updater_policy_test")
    _write_policy(tmp_path / "versions.json", ["40.1.0", "42.0.1"])
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: tmp_path)

    info = _run(module.ElectronRuntimesUpdater().fetch_latest(object()))

    assert info.version == "inventory-v1"
    assert info.metadata == {"versions": ["40.1.0", "42.0.1"]}


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "JSON object"),
        ({"schemaVersion": 2, "versions": ["40.1.0"]}, "schema version"),
        (
            {"schemaVersion": 1, "versions": "40.1.0"},
            "version list must be an array",
        ),
        ({"schemaVersion": 1, "versions": []}, "at least one Electron version"),
        (
            {"schemaVersion": 1, "versions": ["40.1.0", 42]},
            "contain only strings",
        ),
        (
            {"schemaVersion": 1, "versions": ["42.0.1", "40.1.0"]},
            "strictly increasing",
        ),
        (
            {"schemaVersion": 1, "versions": ["40.1.0", "40.1.0"]},
            "strictly increasing",
        ),
        ({"schemaVersion": 1, "versions": ["latest"]}, "exact semver"),
    ],
)
def test_fetch_latest_rejects_malformed_runtime_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: object,
    match: str,
) -> None:
    """Fail closed when the checked-in version policy is ambiguous."""
    module = _load_module(f"electron_runtimes_updater_bad_policy_{match}")
    (tmp_path / "versions.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: tmp_path)

    with pytest.raises((RuntimeError, TypeError), match=match):
        _run(module.ElectronRuntimesUpdater().fetch_latest(object()))


def test_fetch_latest_requires_a_discovered_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed if repository package discovery cannot locate the policy."""
    module = _load_module("electron_runtimes_updater_missing_dir_test")
    monkeypatch.setattr(module, "updater_dir_for", lambda _name: None)

    with pytest.raises(RuntimeError, match="Package directory not found"):
        _run(module.ElectronRuntimesUpdater().fetch_latest(object()))


def test_fetch_hashes_refreshes_every_binary_and_unpacked_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash all four release zips and the unpacked headers for each exact version."""
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

    assert len(binary_urls) == 8
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
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload == [
        HashEntry.create(
            "sha256",
            _HEADER_HASHES[0],
            platform="40.1.0:headers",
        ),
        *[
            HashEntry.create(
                "sha256",
                _BINARY_HASH,
                platform=f"40.1.0:{platform}",
            )
            for platform in updater.PLATFORMS
        ],
        HashEntry.create(
            "sha256",
            _HEADER_HASHES[1],
            platform="42.0.1:headers",
        ),
        *[
            HashEntry.create(
                "sha256",
                _BINARY_HASH,
                platform=f"42.0.1:{platform}",
            )
            for platform in updater.PLATFORMS
        ],
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
    entry = HashEntry.create(
        "sha256",
        _BINARY_HASH,
        platform="42.0.1:headers",
    )
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
            HashEntry.create(
                "sha256",
                _BINARY_HASH,
                platform=f"42.0.1:{artifact}",
            )
            for artifact in ("headers", *updater.PLATFORMS)
        ],
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


def test_fetch_hashes_reuses_a_complete_current_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid re-downloading artifacts when every exact policy record is reusable."""
    module = _load_module("electron_runtimes_updater_reuse_test")
    updater = module.ElectronRuntimesUpdater()
    hashes = [
        HashEntry.create(
            "sha256",
            _BINARY_HASH,
            platform=f"42.0.1:{artifact}",
        )
        for artifact in ("headers", *updater.PLATFORMS)
    ]
    current = SourceEntry(version="inventory-v1", hashes=hashes)

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

    assert events == [UpdateEvent.value(updater.name, hashes)]


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


def test_build_result_persists_only_updater_owned_hash_metadata() -> None:
    """Keep the standard sources.json result free of derivation-side literals."""
    module = _load_module("electron_runtimes_updater_result_test")
    updater = module.ElectronRuntimesUpdater()
    hashes = [
        HashEntry.create(
            "sha256",
            _BINARY_HASH,
            platform="42.0.1:aarch64-darwin",
        )
    ]

    result = updater.build_result(
        module.VersionInfo(
            version="inventory-v1",
            metadata={"versions": ["42.0.1"]},
        ),
        hashes,
    )

    assert result == SourceEntry(version="inventory-v1", hashes=hashes)
