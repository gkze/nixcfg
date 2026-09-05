"""Tests for the GitButler updater."""

from collections.abc import AsyncIterator

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import empty_event_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import FlakeInputMetadata
from lib.update.updaters.node_compatibility import NodejsSelection

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NODEJS_VERSION = "24.11.1"
PNPM_VERSION = "10.34.5"
PACKAGE_MANIFEST = {
    "engines": {"node": ">=24"},
    "packageManager": "pnpm@10.20.0",
}
TOOLCHAIN_PINS = {
    "nodeEngine": ">=24",
    "nodejsAttr": "nodejs_24",
    "nodejsVersion": NODEJS_VERSION,
    "packageManager": "pnpm@10.20.0",
    "pnpmAttr": "pnpm_10",
    "pnpmVersion": PNPM_VERSION,
}


def _load_module(module_name: str):
    return load_repo_module("packages/gitbutler/updater.py", module_name)


def _version_info(version: str = "0.19.9") -> VersionInfo:
    return VersionInfo(version=version, metadata=TOOLCHAIN_PINS)


def _flake_node(
    *,
    ref: str | None = "release/0.19.9",
    rev: str | None = "a" * 40,
) -> FlakeLockNode:
    payload: dict[str, object] = {}
    if ref is not None:
        payload["original"] = {
            "type": "github",
            "owner": "gitbutlerapp",
            "repo": "gitbutler",
            "ref": ref,
        }
    if rev is not None:
        payload["locked"] = {
            "type": "github",
            "owner": "gitbutlerapp",
            "repo": "gitbutler",
            "rev": rev,
            "narHash": HASH,
        }
    return FlakeLockNode.model_validate(payload)


def _mock_toolchain_resolution(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_command_timeout: float,
    manifest: object = PACKAGE_MANIFEST,
) -> tuple[list[tuple[str, int, str]], list[FlakeLockNode]]:
    source_reads: list[tuple[str, int, str]] = []
    resolved_nodes: list[FlakeLockNode] = []

    class _Source:
        async def read_json(
            self,
            relative_path: str,
            *,
            max_bytes: int,
            description: str,
        ) -> object:
            source_reads.append((relative_path, max_bytes, description))
            return manifest

    async def _resolve_locked_source(
        node: FlakeLockNode,
        *,
        context: str,
        command_timeout: float,
    ) -> _Source:
        assert context == "GitButler flake input"
        assert command_timeout > 0
        resolved_nodes.append(node)
        return _Source()

    async def _resolve_package_version(
        package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        assert source_name == "GitButler"
        assert command_timeout == expected_command_timeout
        assert package_attr == "pnpm_10"
        return PNPM_VERSION

    async def _resolve_nodejs(
        engine: object,
        *,
        command_timeout: float,
        source_name: str,
    ) -> NodejsSelection:
        assert source_name == "GitButler"
        assert command_timeout == expected_command_timeout
        assert isinstance(engine, str)
        return NodejsSelection(
            engine=engine,
            attribute="nodejs_24",
            version=NODEJS_VERSION,
        )

    monkeypatch.setattr(module, "resolve_locked_source", _resolve_locked_source)
    monkeypatch.setattr(
        module,
        "resolve_nixpkgs_package_version",
        _resolve_package_version,
    )
    monkeypatch.setattr(
        module,
        "resolve_nixpkgs_nodejs_for_engine",
        _resolve_nodejs,
    )
    return source_reads, resolved_nodes


def test_gitbutler_updater_tracks_release_ref_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package version should come from the locked release ref."""
    module = _load_module("gitbutler_updater_latest_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="b" * 40)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    source_reads, resolved_nodes = _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=updater.config.default_subprocess_timeout,
    )

    info = _run(updater.fetch_latest(object()))

    assert module.GitButlerUpdater.hash_type == "npmDepsHash"
    assert module.GitButlerUpdater.input_name == "gitbutler"
    assert module.GitButlerUpdater.supported_platforms == (
        "aarch64-darwin",
        "x86_64-linux",
    )
    assert info.version == "0.19.9"
    assert info.commit == "b" * 40
    assert info.metadata == {
        **FlakeInputMetadata(node=node, commit="b" * 40).to_dict(),
        **TOOLCHAIN_PINS,
    }
    assert updater.source_pins_for(info) == TOOLCHAIN_PINS
    assert resolved_nodes == [node]
    assert source_reads == [
        (
            "package.json",
            module._MAX_MANIFEST_BYTES,
            "package manifest",
        )
    ]


def test_gitbutler_updater_requires_immutable_locked_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest must come from the immutable tree used by the build."""
    module = _load_module("gitbutler_updater_no_locked_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev=None)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="must resolve to an immutable"):
        _run(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    "node",
    [
        _flake_node(ref="main"),
        _flake_node(ref=None),
    ],
)
def test_gitbutler_updater_requires_release_ref(
    monkeypatch: pytest.MonkeyPatch,
    node: FlakeLockNode,
) -> None:
    """Unexpected flake input refs should fail before hashing."""
    module = _load_module("gitbutler_updater_bad_ref_test")
    updater = module.GitButlerUpdater()

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="must be pinned to a release"):
        _run(updater.fetch_latest(object()))


@pytest.mark.parametrize(
    ("manifest", "error_type", "message"),
    [
        ([], TypeError, "is not a JSON object"),
        ({}, TypeError, "Node engines are missing"),
        (
            {
                "engines": {},
                "packageManager": "pnpm@10.20.0",
            },
            TypeError,
            "Node engine is missing",
        ),
        (
            {
                "engines": {"node": "workspace:*"},
                "packageManager": "pnpm@10.20.0",
            },
            RuntimeError,
            "valid npm semantic-version range",
        ),
        (
            {"engines": {"node": ">=24"}},
            TypeError,
            "packageManager is missing",
        ),
        (
            {"engines": {"node": ">=24"}, "packageManager": "npm@11.0.0"},
            RuntimeError,
            "must select an exact pnpm",
        ),
    ],
)
def test_gitbutler_updater_rejects_invalid_toolchain_manifests(
    monkeypatch: pytest.MonkeyPatch,
    manifest: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Malformed or ambiguous upstream toolchain contracts fail before hashing."""
    module = _load_module("gitbutler_updater_invalid_toolchain_test")

    async def _unexpected_resolve(
        _package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        _ = command_timeout
        pytest.fail(f"unexpected package resolution for {source_name}")

    monkeypatch.setattr(
        module,
        "resolve_nixpkgs_package_version",
        _unexpected_resolve,
    )

    with pytest.raises(error_type, match=message):
        _run(
            module.GitButlerUpdater._toolchain_pins(
                manifest,
                command_timeout=19,
            )
        )


def test_gitbutler_updater_accepts_corepack_integrity_and_newer_compatible_pnpm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corepack integrity suffixes do not obscure the exact required pnpm version."""
    module = _load_module("gitbutler_updater_corepack_toolchain_test")
    manifest = {
        "engines": {"node": ">=24.1"},
        "packageManager": "pnpm@10.20.0+sha512.AbCd_0123=-",
    }
    _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=19,
        manifest=manifest,
    )

    assert _run(
        module.GitButlerUpdater._toolchain_pins(manifest, command_timeout=19)
    ) == {
        **TOOLCHAIN_PINS,
        "nodeEngine": ">=24.1",
        "packageManager": "pnpm@10.20.0+sha512.AbCd_0123=-",
    }


def test_gitbutler_updater_rejects_incompatible_selected_pnpm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lagging nixpkgs pnpm fails before the dependency hash build."""
    module = _load_module("gitbutler_updater_incompatible_pnpm_test")
    _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=19,
    )

    async def _resolve_package_version(
        package_attr: str,
        *,
        command_timeout: float,
        source_name: str,
    ) -> str:
        assert source_name == "GitButler"
        assert command_timeout == 19
        assert package_attr == "pnpm_10"
        return "10.19.0"

    monkeypatch.setattr(
        module,
        "resolve_nixpkgs_package_version",
        _resolve_package_version,
    )

    with pytest.raises(RuntimeError, match="does not satisfy"):
        _run(
            module.GitButlerUpdater._toolchain_pins(
                PACKAGE_MANIFEST,
                command_timeout=19,
            )
        )


def test_gitbutler_updater_requires_complete_toolchain_metadata() -> None:
    """Partial toolchain metadata cannot silently reach package evaluation."""
    module = _load_module("gitbutler_updater_partial_toolchain_test")
    updater = module.GitButlerUpdater()
    info = VersionInfo(
        version="0.19.9",
        metadata={
            key: value for key, value in TOOLCHAIN_PINS.items() if key != "pnpmAttr"
        },
    )

    with pytest.raises(TypeError, match="pnpmAttr"):
        updater.source_pins_for(info)


def test_gitbutler_updater_builds_pnpm_hash_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash probes should target the locked GitButler pnpm dependency cache."""
    module = _load_module("gitbutler_updater_hash_expr_test")
    updater = module.GitButlerUpdater()
    captured: dict[str, object] = {}

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({"name": name, "expr": expr, "config": config})
        yield UpdateEvent.status(name, "hashing pnpm deps")
        yield UpdateEvent.value(name, HASH)

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect(
            updater._compute_hash_for_system(
                _version_info(),
                system="aarch64-darwin",
            )
        )
    )

    assert events == [
        UpdateEvent.status("gitbutler", "hashing pnpm deps"),
        UpdateEvent.value("gitbutler", HASH),
    ]
    assert captured["name"] == "gitbutler"
    assert captured["config"] is updater.config
    assert captured["expr"] == _build_package_path_attr_expr(
        "gitbutler",
        ".frontend.pnpmDeps",
        system="aarch64-darwin",
        source_overrides={"gitbutler": updater.build_result(_version_info(), [])},
        fake_hashes=True,
    )


def test_gitbutler_updater_streams_artifacts_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked-in crate2nix artifacts should refresh before the pnpm hash."""
    module = _load_module("gitbutler_updater_artifact_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="d" * 40)

    async def _artifacts() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("gitbutler", "materialized cargo artifacts")

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.status(name, "hashing pnpm deps")
        yield UpdateEvent.value(name, HASH)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=updater.config.default_subprocess_timeout,
    )
    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(_version_info(), object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "materialized cargo artifacts"
    assert events[1].message == "hashing pnpm deps"
    assert events[2].payload == [HashEntry.create("npmDepsHash", HASH)]


def test_gitbutler_update_recomputes_pnpm_drv_hash_when_cargo_artifacts_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cargo artifacts do not invalidate the independent pnpm derivation hash."""
    module = _load_module("gitbutler_updater_artifact_drv_hash_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="f" * 40)
    old_hash = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    current = SourceEntry.model_validate({
        "version": "0.19.8",
        "drvHash": "old-drv",
        "hashes": [
            {
                "hashType": "npmDepsHash",
                "hash": old_hash,
            }
        ],
        "input": "gitbutler",
    })

    async def _artifacts() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.artifact(
            "gitbutler",
            GeneratedArtifact.text("packages/gitbutler/Cargo.nix", "updated"),
        )

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.value(name, HASH)

    fingerprint_calls = 0

    async def _compute_drv_fingerprint(*_args: object, **_kwargs: object) -> str:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return "pre-artifact-drv"

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=updater.config.default_subprocess_timeout,
    )
    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(
        "lib.update.nix.compute_expr_drv_fingerprint",
        _compute_drv_fingerprint,
    )

    events = _run(_collect(updater.update_stream(current, object())))

    result_events = [event for event in events if event.kind == UpdateEventKind.RESULT]
    assert len(result_events) == 1
    result = result_events[0].payload
    assert isinstance(result, SourceEntry)
    assert result.version == "0.19.9"
    assert result.drv_hash == "pre-artifact-drv"
    assert result.hashes.entries == [HashEntry.create("npmDepsHash", HASH)]
    assert result.pins == TOOLCHAIN_PINS
    assert fingerprint_calls == 1


def test_gitbutler_updater_requires_npm_deps_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty fixed-output stream should fail instead of writing no hash."""
    module = _load_module("gitbutler_updater_missing_hash_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="e" * 40)

    async def _missing_hash(
        _name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        async for event in empty_event_stream():
            yield event

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", empty_event_stream)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _missing_hash)

    with pytest.raises(RuntimeError, match="Missing npmDepsHash output"):
        _run(_collect(updater.fetch_hashes(_version_info(), object())))
