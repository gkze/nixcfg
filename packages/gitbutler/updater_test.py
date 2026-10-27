"""Tests for the GitButler updater."""

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import (
    empty_event_stream,
    load_repo_module,
)
from lib.tests._updater_helpers import run_async as _run
from lib.update import nix as update_nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.candidate import ResolvedVersion
from lib.update.ci import candidate as candidate_pipeline
from lib.update.events import EventSink, UpdateEvent, UpdateEventKind, ignore_event
from lib.update.nix import PreparedProbe, _build_package_path_attr_expr
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.node_compatibility import NodejsSelection

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
USABLE_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
FOREIGN_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="
OTHER_HASH = "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="
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


@pytest.fixture(autouse=True)
def _prepared_probe_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fingerprint(*_args, **_kwargs):
        return "probe-drv"

    async def prepare(source, expressions, *, config, **_kwargs):
        return {
            key: PreparedProbe(
                "/nix/store/gitbutler.drv",
                await update_nix.compute_expr_drv_fingerprint(
                    source, expression, config=config
                ),
                expression,
            )
            for key, expression in expressions.items()
        }

    monkeypatch.setattr(update_nix, "compute_expr_drv_fingerprint", fingerprint)
    monkeypatch.setattr(update_nix, "prepare_fixed_output_probes", prepare)


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

    info = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert module.GitButlerUpdater.hash_type == "npmDepsHash"
    assert module.GitButlerUpdater.input_name == "gitbutler"
    assert module.GitButlerUpdater.platform_specific is True
    assert module.GitButlerUpdater.native_only is True
    assert module.GitButlerUpdater.supported_platforms == (
        "aarch64-darwin",
        "x86_64-linux",
    )
    updater.native_only = False
    assert updater._platform_targets("aarch64-darwin") == ("aarch64-darwin",)
    assert info.version == "0.19.9"
    assert info.commit == "b" * 40
    assert info.metadata == {
        "node": node.model_dump(mode="json"),
        "commit": "b" * 40,
        **TOOLCHAIN_PINS,
    }
    assert updater.source_pins_for(info) == TOOLCHAIN_PINS
    restored = ResolvedVersion.model_validate_json(
        ResolvedVersion.capture(info).model_dump_json()
    ).restore()
    assert restored == info
    assert updater.source_pins_for(restored) == TOOLCHAIN_PINS
    assert module.GitButlerUpdater._resolve_flake_node(updater, restored) == node
    assert resolved_nodes == [node]
    assert source_reads == [
        (
            "package.json",
            module._MAX_MANIFEST_BYTES,
            "package manifest",
        )
    ]


@pytest.mark.parametrize(
    ("rev", "match"),
    [
        (None, "must resolve to a complete GitHub source"),
        ("main", "revision must be an immutable commit"),
    ],
)
def test_gitbutler_updater_requires_immutable_locked_commit(
    monkeypatch: pytest.MonkeyPatch,
    rev: str | None,
    match: str,
) -> None:
    """The manifest must come from the immutable tree used by the build.

    Both rejection shapes come from the shared locked-source owner: a missing
    revision is an incomplete GitHub source, a mutable one is not a commit.
    """
    module = _load_module("gitbutler_updater_no_locked_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev=rev)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


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
        _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


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
        emit: EventSink = ignore_event,
    ) -> object:
        captured.update({"name": name, "expr": expr, "config": config})
        await emit(UpdateEvent.status(name, "hashing pnpm deps"))
        return HASH

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect(
            lambda emit: updater._compute_hash_for_system(
                _version_info(), system="aarch64-darwin", emit=emit
            )
        )
    )

    assert events == [UpdateEvent.status("gitbutler", "hashing pnpm deps")]
    assert events.result == HASH
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

    async def _artifacts(*, emit: EventSink = ignore_event) -> object:
        await emit(UpdateEvent.status("gitbutler", "materialized cargo artifacts"))

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
        emit: EventSink = ignore_event,
    ) -> object:
        _ = config
        await emit(UpdateEvent.status(name, "hashing pnpm deps"))
        return HASH

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    _mock_toolchain_resolution(
        module,
        monkeypatch,
        expected_command_timeout=updater.config.default_subprocess_timeout,
    )
    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                _version_info(),
                object(),
                emit=emit,
                context=UpdateContext(current=None),
            )
        )
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
    ]
    assert events[0].message == "materialized cargo artifacts"
    assert events[1].message == "hashing pnpm deps"
    assert events.result == [
        HashEntry.create("npmDepsHash", HASH, platform="aarch64-darwin")
    ]


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

    async def _artifacts(*, emit: EventSink = ignore_event) -> object:
        await emit(
            UpdateEvent.artifact(
                "gitbutler",
                GeneratedArtifact.text("packages/gitbutler/Cargo.nix", "updated"),
            )
        )

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
        emit: EventSink = ignore_event,
    ) -> object:
        _ = config
        return HASH

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
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(lambda emit: updater.update_stream(current, object(), emit=emit))
    )

    result_events = [event for event in events if event.kind == UpdateEventKind.RESULT]
    assert len(result_events) == 1
    result = result_events[0].payload
    assert isinstance(result, SourceEntry)
    assert result.version == "0.19.9"
    assert result.drv_hash == "pre-artifact-drv"
    assert result.hashes.entries == [
        HashEntry.create("npmDepsHash", HASH, platform="aarch64-darwin")
    ]
    assert result.platform_drv_hashes == {"aarch64-darwin": "pre-artifact-drv"}
    assert result.pins == TOOLCHAIN_PINS
    assert fingerprint_calls == 1


def test_gitbutler_updater_propagates_npm_hash_failure(
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
        emit: EventSink = ignore_event,
    ) -> object:
        _ = config
        raise RuntimeError("npm hash probe failed")

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", empty_event_stream)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _missing_hash)

    with pytest.raises(RuntimeError, match="npm hash probe failed"):
        _run(
            _collect(
                lambda emit: updater.fetch_hashes(
                    _version_info(),
                    object(),
                    emit=emit,
                    context=UpdateContext(current=None),
                )
            )
        )


def _platform_source(
    *,
    version: str = "0.19.9",
    pins: dict[str, str] | None = None,
    hashes: list[HashEntry] | None = None,
    platform_drv_hashes: dict[str, str] | None = None,
    electron_version: str | None = None,
    drv_hash: str | None = "probe-drv",
) -> SourceEntry:
    return SourceEntry(
        version=version,
        input="gitbutler",
        pins=TOOLCHAIN_PINS if pins is None else pins,
        hashes=HashCollection(entries=hashes or []),
        platform_drv_hashes=platform_drv_hashes,
        electron_version=electron_version,
        drv_hash=drv_hash,
    )


def test_gitbutler_fetch_hashes_keeps_foreign_platform_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native rehash replaces this host and drops the old unqualified hash."""
    module = _load_module("gitbutler_updater_preserve_platform_test")
    updater = module.GitButlerUpdater()
    foreign = HashEntry.create(
        "npmDepsHash",
        FOREIGN_HASH,
        platform="x86_64-linux",
    )
    other = HashEntry.create("srcHash", OTHER_HASH)
    current = _platform_source(
        hashes=[
            HashEntry.create("npmDepsHash", USABLE_HASH),
            HashEntry.create("npmDepsHash", USABLE_HASH, platform="aarch64-darwin"),
            foreign,
            other,
        ],
        platform_drv_hashes={
            "aarch64-darwin": "stale-darwin",
            "x86_64-linux": "x86-cert",
        },
    )
    context = UpdateContext(current=current)

    async def _fixed_hash(*_args: object, **_kwargs: object) -> str:
        return HASH

    monkeypatch.setattr(updater, "stream_materialized_artifacts", empty_event_stream)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                _version_info(),
                object(),
                emit=emit,
                context=context,
            )
        )
    )

    assert events.result == [
        foreign,
        other,
        HashEntry.create("npmDepsHash", HASH, platform="aarch64-darwin"),
    ]
    assert context.drv_fingerprints == {
        "aarch64-darwin": "probe-drv",
        "x86_64-linux": "x86-cert",
    }


def test_gitbutler_fetch_hashes_accepts_a_mapping_only_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source with no hash entries still records this host's pnpm hash."""
    module = _load_module("gitbutler_updater_mapping_source_test")
    updater = module.GitButlerUpdater()
    current = SourceEntry(
        version="0.19.9",
        input="gitbutler",
        hashes=HashCollection(mapping={"aarch64-darwin": USABLE_HASH}),
    )
    context = UpdateContext(current=current)

    async def _fixed_hash(*_args: object, **_kwargs: object) -> str:
        return HASH

    monkeypatch.setattr(updater, "stream_materialized_artifacts", empty_event_stream)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    events = _run(
        _collect(
            lambda emit: updater.fetch_hashes(
                _version_info(),
                object(),
                emit=emit,
                context=context,
            )
        )
    )

    assert events.result == [
        HashEntry.create("npmDepsHash", HASH, platform="aarch64-darwin")
    ]
    assert context.drv_fingerprints == {"aarch64-darwin": "probe-drv"}


def test_gitbutler_platform_hashes_union_across_native_prepares() -> None:
    """Complementary pnpm hashes merge; one unqualified hash does not."""
    darwin = _platform_source(
        version="0.23.0",
        hashes=[
            HashEntry.create("npmDepsHash", USABLE_HASH, platform="aarch64-darwin")
        ],
        platform_drv_hashes={"aarch64-darwin": "darwin-drv"},
        drv_hash="darwin-drv",
    )
    x86 = _platform_source(
        version="0.23.0",
        hashes=[HashEntry.create("npmDepsHash", FOREIGN_HASH, platform="x86_64-linux")],
        platform_drv_hashes={"x86_64-linux": "x86-drv"},
        drv_hash="x86-drv",
    )

    assert candidate_pipeline._source_entries_compatible(darwin, x86)
    merged = darwin.merge(x86)
    assert merged.hashes.entries == [
        HashEntry.create("npmDepsHash", USABLE_HASH, platform="aarch64-darwin"),
        HashEntry.create("npmDepsHash", FOREIGN_HASH, platform="x86_64-linux"),
    ]
    assert merged.platform_drv_hashes == {
        "aarch64-darwin": "darwin-drv",
        "x86_64-linux": "x86-drv",
    }
    collided = darwin.model_copy(
        update={
            "hashes": HashCollection(
                entries=[HashEntry.create("npmDepsHash", USABLE_HASH)]
            )
        }
    )
    other = x86.model_copy(
        update={
            "hashes": HashCollection(
                entries=[HashEntry.create("npmDepsHash", FOREIGN_HASH)]
            )
        }
    )
    assert not candidate_pipeline._source_entries_compatible(collided, other)


@pytest.mark.parametrize(
    "current",
    [
        None,
        _platform_source(version="0.19.8"),
        _platform_source(pins={**TOOLCHAIN_PINS, "pnpmVersion": "10.0.0"}),
        _platform_source(electron_version="40.0.0"),
        _platform_source(
            hashes=[
                HashEntry.create(
                    "npmDepsHash",
                    USABLE_HASH,
                    platform="x86_64-linux",
                )
            ]
        ),
        _platform_source(
            hashes=[HashEntry.create("npmDepsHash", HASH, platform="aarch64-darwin")]
        ),
    ],
)
def test_gitbutler_is_latest_rejects_incomplete_native_state(
    monkeypatch: pytest.MonkeyPatch,
    current: SourceEntry | None,
) -> None:
    """Version, pins, and a usable native hash are required before the probe."""
    module = _load_module("gitbutler_updater_stale_latest_test")
    updater = module.GitButlerUpdater()
    probed = False

    async def _probe(*_args: object, **_kwargs: object) -> str:
        nonlocal probed
        probed = True
        return "probe-drv"

    monkeypatch.setattr(updater, "_prepare_hash_probes", _probe)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    assert not _run(updater._is_latest(UpdateContext(current=current), _version_info()))
    assert not probed


def test_gitbutler_is_latest_follows_this_platforms_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching native certificate skips the pnpm fetch."""
    module = _load_module("gitbutler_updater_fresh_latest_test")
    updater = module.GitButlerUpdater()
    current = _platform_source(
        hashes=[
            HashEntry.create(
                "npmDepsHash",
                USABLE_HASH,
                platform="aarch64-darwin",
            )
        ],
        platform_drv_hashes={"aarch64-darwin": "probe-drv"},
    )
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    assert _run(updater._is_latest(UpdateContext(current=current), _version_info()))

    stale = current.model_copy(
        update={"platform_drv_hashes": {"aarch64-darwin": "other-drv"}}
    )
    assert not _run(updater._is_latest(UpdateContext(current=stale), _version_info()))


def test_gitbutler_is_latest_treats_probe_failure_as_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint probe that cannot run must recompute the hash."""
    module = _load_module("gitbutler_updater_probe_failure_test")
    updater = module.GitButlerUpdater()
    current = _platform_source(
        hashes=[
            HashEntry.create(
                "npmDepsHash",
                USABLE_HASH,
                platform="aarch64-darwin",
            )
        ],
        platform_drv_hashes={"aarch64-darwin": "probe-drv"},
    )

    async def _probe(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("probe unavailable")

    monkeypatch.setattr(updater, "_prepare_hash_probes", _probe)
    monkeypatch.setattr(
        "lib.update.nix.get_current_nix_platform",
        lambda: "aarch64-darwin",
    )

    assert not _run(updater._is_latest(UpdateContext(current=current), _version_info()))
