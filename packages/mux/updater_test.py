"""Behavioral tests for Mux's release-derived Electron source identity."""

import json
from types import ModuleType

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events, load_repo_module, run_async
from lib.update.events import UpdateEvent
from lib.update.net import github_raw_url
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters import VersionInfo

_VERSION = "2.3.4"
_REF = f"v{_VERSION}"
_COMMIT = "b" * 40
_ELECTRON_SPEC = "^43.2.0"
_ELECTRON_VERSION = "43.4.5"
_BUN_VERSION = "1.3.14"
_HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_HASH_C = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
_NODE_HASHES = {
    "aarch64-darwin": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
    "aarch64-linux": "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
    "x86_64-linux": "sha256-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF=",
}


@pytest.fixture(scope="module")
def updater_module() -> ModuleType:
    """Load the updater from its repository path."""
    return load_repo_module(
        "packages/mux/updater.py",
        "mux_updater_dedicated_test",
    )


def _flake_node(
    *,
    source_type: str = "github",
    commit: str = _COMMIT,
) -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": source_type,
            "owner": "example",
            "repo": "mux",
            "rev": commit,
            "narHash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        },
        "original": {
            "type": source_type,
            "owner": "example",
            "repo": "mux",
            "ref": _REF,
        },
    })


def _manifest(
    *,
    name: str = "mux",
    version: str = _VERSION,
    electron_spec: str = _ELECTRON_SPEC,
    package_manager: str = f"bun@{_BUN_VERSION}",
) -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "packageManager": package_manager,
        "optionalDependencies": {"electron": electron_spec},
    }


def _lock_payload(
    *,
    name: str = "mux",
    electron_spec: str = _ELECTRON_SPEC,
    resolution: object = f"electron@{_ELECTRON_VERSION}",
) -> bytes:
    return json.dumps({
        "lockfileVersion": 1,
        "workspaces": {
            "": {
                "name": name,
                "optionalDependencies": {"electron": electron_spec},
            },
        },
        "packages": {"electron": [resolution, "", {}]},
    }).encode()


def test_fetch_latest_derives_identity_from_immutable_manifest_and_lock(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _flake_node()
    fetched_urls: list[str] = []

    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "mux" else None,
    )

    async def fetch_json(_session, url: str, **_kwargs):
        fetched_urls.append(url)
        return _manifest()

    async def fetch_url(_session, url: str, **_kwargs):
        fetched_urls.append(url)
        return _lock_payload()

    monkeypatch.setattr(updater_module, "fetch_json", fetch_json)
    monkeypatch.setattr(updater_module, "fetch_url", fetch_url)

    updater = updater_module.MuxUpdater()
    info = run_async(updater.fetch_latest(object()))

    assert info.version == _REF
    assert isinstance(info.metadata, updater_module.MuxSourceMetadata)
    assert info.metadata.to_dict() == {
        "node": node,
        "commit": _COMMIT,
        "bunVersion": _BUN_VERSION,
        "electronVersion": _ELECTRON_VERSION,
    }
    assert info.commit == _COMMIT
    assert updater.compatibility_pins is None
    result = updater.build_result(info, [])
    assert result.electron_version == _ELECTRON_VERSION
    assert result.pins == {"bunVersion": _BUN_VERSION}
    # A legacy source using pins must refresh into the typed top-level field.
    stale = SourceEntry(
        version=_REF,
        input="mux",
        hashes=[],
        pins={"electronVersion": _ELECTRON_VERSION},
        drv_hash="unchanged-fingerprint",
    )
    assert run_async(updater._is_latest(stale, info)) is False
    assert fetched_urls == [
        github_raw_url("example", "mux", _COMMIT, "package.json"),
        github_raw_url("example", "mux", _COMMIT, "bun.lock"),
    ]


@pytest.mark.parametrize(
    ("manifest", "lock_payload", "message"),
    [
        (_manifest(version="9.9.9"), _lock_payload(), "manifest version"),
        (_manifest(), _lock_payload(name="other"), "workspace name"),
        (
            _manifest(),
            _lock_payload(electron_spec="~43.2.0"),
            "Electron spec mismatch",
        ),
        (
            _manifest(),
            _lock_payload(resolution="electron@^43.4.5"),
            "exact semantic version",
        ),
    ],
)
def test_release_contract_rejects_mismatched_or_inexact_metadata(
    updater_module: ModuleType,
    manifest: dict[str, object],
    lock_payload: bytes,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        updater_module._resolve_release_contract(
            release_version=_VERSION,
            manifest=manifest,
            lock_payload=lock_payload,
        )


def test_release_contract_rejects_locked_electron_outside_manifest_range(
    updater_module: ModuleType,
) -> None:
    """Do not accept a lock resolution outside the declared npm range."""
    electron_spec = "^40.9.3"

    with pytest.raises(RuntimeError, match="does not satisfy"):
        updater_module._resolve_release_contract(
            release_version=_VERSION,
            manifest=_manifest(electron_spec=electron_spec),
            lock_payload=_lock_payload(
                electron_spec=electron_spec,
                resolution="electron@41.0.0",
            ),
        )


def test_bun_lock_rejects_invalid_encoding(updater_module: ModuleType) -> None:
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        updater_module._bun_lock_contract(b"\xff")


def test_fetch_hashes_uses_exact_bun_sources_for_node_modules_probes(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested Bun downloads stay real while the outer cache hash is probed."""
    node = _flake_node()
    info = VersionInfo(
        version=_REF,
        metadata=updater_module.MuxSourceMetadata(
            node=node,
            commit=_COMMIT,
            bun_version=_BUN_VERSION,
            electron_version=_ELECTRON_VERSION,
        ),
    )
    seen_candidates: dict[str, SourceEntry] = {}

    async def compute_url_hashes(_source, urls, **_kwargs):
        url_list = list(urls)
        yield UpdateEvent.value(
            "mux",
            dict(zip(url_list, (_HASH_A, _HASH_B, _HASH_C), strict=True)),
        )

    def compute_node_hash(self, candidate_info, *, system):
        candidate = self.build_result(candidate_info, [])
        seen_candidates[system] = candidate

        async def events():
            yield UpdateEvent.value("mux", _NODE_HASHES[system])

        return events()

    monkeypatch.setattr(
        updater_module.update_process,
        "compute_url_hashes",
        compute_url_hashes,
    )
    monkeypatch.setattr(
        updater_module.MuxUpdater,
        "_compute_hash_for_system",
        compute_node_hash,
    )
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = run_async(
        collect_events(updater_module.MuxUpdater().fetch_hashes(info, object()))
    )
    hashes = events[-1].payload

    assert isinstance(hashes, list)
    assert [entry.hash_type for entry in hashes] == [
        "bunRuntimeHash",
        "bunRuntimeHash",
        "bunRuntimeHash",
        "nodeModulesHash",
        "nodeModulesHash",
        "nodeModulesHash",
    ]
    assert set(seen_candidates) == {
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }
    for candidate in seen_candidates.values():
        assert candidate.pins == {"bunVersion": _BUN_VERSION}
        assert candidate.hashes.entries is not None
        assert [entry.hash_type for entry in candidate.hashes.entries] == [
            "bunRuntimeHash",
            "bunRuntimeHash",
            "bunRuntimeHash",
        ]


@pytest.mark.parametrize(
    ("resolution", "exception", "message"),
    [
        (None, TypeError, "no string resolution"),
        ("runtime@43.4.5", RuntimeError, "resolution is malformed"),
    ],
)
def test_bun_lock_requires_a_named_exact_electron_resolution(
    updater_module: ModuleType,
    resolution: object,
    exception: type[Exception],
    message: str,
) -> None:
    with pytest.raises(exception, match=message):
        updater_module._bun_lock_contract(_lock_payload(resolution=resolution))


def test_bun_lock_requires_its_root_workspace(updater_module: ModuleType) -> None:
    payload = json.dumps({"workspaces": {}, "packages": {}}).encode()
    with pytest.raises(TypeError, match="root workspace"):
        updater_module._bun_lock_contract(payload)


@pytest.mark.parametrize(
    ("node", "message"),
    [
        (_flake_node(source_type="gitlab"), "complete GitHub source"),
        (_flake_node(commit="not-a-commit"), "immutable commit"),
    ],
)
def test_source_resolution_requires_an_immutable_github_commit(
    updater_module: ModuleType,
    node: FlakeLockNode,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        updater_module._locked_github_source(node)


def test_version_and_source_identity_metadata_fail_closed(
    updater_module: ModuleType,
) -> None:
    updater = updater_module.MuxUpdater()
    with pytest.raises(RuntimeError, match="exact semantic version"):
        updater_module._release_version("main")
    with pytest.raises(TypeError, match="resolved release source"):
        updater.build_result(VersionInfo(version=_REF), [])


def _release_info(updater_module: ModuleType) -> VersionInfo:
    return VersionInfo(
        version=_REF,
        metadata=updater_module.MuxSourceMetadata(
            node=_flake_node(),
            commit=_COMMIT,
            bun_version=_BUN_VERSION,
            electron_version=_ELECTRON_VERSION,
        ),
    )


def _runtime_hashes(updater_module: ModuleType) -> list[HashEntry]:
    urls = updater_module.bun_release_urls(
        _BUN_VERSION, updater_module._SUPPORTED_SYSTEMS
    )
    return updater_module.bun_runtime_hash_entries(
        _BUN_VERSION,
        updater_module._SUPPORTED_SYSTEMS,
        dict.fromkeys(urls.values(), _HASH_A),
    )


@pytest.mark.parametrize("current", [None, SourceEntry(version=_REF, hashes={})])
def test_missing_structured_runtime_sources_force_refresh(
    updater_module: ModuleType,
    current: SourceEntry | None,
) -> None:
    """Legacy metadata cannot certify the runtime used for dependency hashing."""
    assert not run_async(
        updater_module.MuxUpdater()._is_latest(current, _release_info(updater_module))
    )


def test_reusable_fingerprint_preserves_runtime_sources_and_normalizes_cache_hash(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged exact Bun runtime remains available to fingerprint evaluation."""
    updater = updater_module.MuxUpdater()
    info = _release_info(updater_module)
    runtime_hashes = _runtime_hashes(updater_module)
    current = updater.build_result(
        info,
        [
            *runtime_hashes,
            HashEntry.create("nodeModulesHash", _HASH_B, platform="aarch64-darwin"),
        ],
    ).model_copy(update={"drv_hash": "current-fingerprint"})
    normalized = current.model_copy(
        update={"drv_hash": None, "hashes": HashCollection(entries=runtime_hashes)}
    )
    expressions: list[str] = []

    async def _fingerprint(source: str, expression: str, **_kwargs: object) -> str:
        assert source == "mux"
        expressions.append(expression)
        assert_nix_ast_equal(
            expression,
            _build_package_path_attr_expr(
                "mux",
                ".offlineCache",
                source_overrides={"mux": normalized},
                fake_hashes=True,
            ),
        )
        return "current-fingerprint"

    monkeypatch.setattr("lib.update.nix.compute_expr_drv_fingerprint", _fingerprint)
    assert run_async(updater._is_latest(current, info)) is True
    assert run_async(updater._compute_drv_fingerprint(current)) == "current-fingerprint"
    assert len(expressions) == 2


@pytest.mark.parametrize(
    ("failure", "error", "message"),
    [
        ("runtime", RuntimeError, "Missing Mux Bun runtime hashes"),
        ("node", RuntimeError, "Missing Mux node_modules hashes"),
        ("invalid-node", TypeError, "must use structured hash entries"),
    ],
)
def test_incomplete_hash_producers_cannot_yield_a_candidate(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    error: type[Exception],
    message: str,
) -> None:
    """Missing or malformed probe output cannot be persisted as a complete update."""

    async def _runtime_hashes(_source, urls, **_kwargs):
        yield UpdateEvent.status("mux", "probing runtime")
        if failure != "runtime":
            yield UpdateEvent.value("mux", dict.fromkeys(urls, _HASH_A))

    async def _node_hashes(_self, _info, _session, **_kwargs):
        yield UpdateEvent.status("mux", "probing dependencies")
        if failure == "invalid-node":
            yield UpdateEvent.value("mux", {"aarch64-darwin": _HASH_B})

    monkeypatch.setattr(
        updater_module.update_process, "compute_url_hashes", _runtime_hashes
    )
    monkeypatch.setattr(
        updater_module.BunNodeModulesHashUpdater, "fetch_hashes", _node_hashes
    )
    with pytest.raises(error, match=message):
        run_async(
            collect_events(
                updater_module.MuxUpdater().fetch_hashes(
                    _release_info(updater_module), object()
                )
            )
        )


@pytest.mark.parametrize("duplicate_runtime", [False, True])
def test_candidate_hashes_cannot_replace_or_duplicate_resolved_runtime_hashes(
    updater_module: ModuleType,
    duplicate_runtime: bool,
) -> None:
    """A dependency probe must not overwrite the previously hashed Bun sources."""
    runtime_hashes = _runtime_hashes(updater_module)
    info = updater_module._with_bun_runtime_hashes(
        _release_info(updater_module), runtime_hashes
    )
    if duplicate_runtime:
        with pytest.raises(RuntimeError, match="duplicate Bun runtime hashes"):
            updater_module.MuxUpdater().build_result(info, runtime_hashes)
    else:
        with pytest.raises(TypeError, match="structured hash entries"):
            updater_module.MuxUpdater().build_result(info, {"aarch64-darwin": _HASH_B})
