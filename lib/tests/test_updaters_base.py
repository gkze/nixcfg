"""Tests for shared updater base behavior."""

import asyncio
from typing import ClassVar
from unittest.mock import patch

import aiohttp
import pytest

from lib.nix.commands.base import CommandResult as NixCommandResultData
from lib.nix.commands.base import NixCommandError
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update.config import resolve_config
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters import (
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    FlakeInputMetadataUpdater,
    HashEntryUpdater,
    UvLockUpdater,
    VersionInfo,
)
from lib.update.updaters.core import stream_source_then_overlay_hashes
from lib.update.updaters.metadata import require_metadata_str


class _ConfiguredDownloadUpdater(DownloadHashUpdater):
    name = "configured-download"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://example.com/archive.tar.gz"
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0")


def test_updater_rejects_ambiguous_static_source_pins() -> None:
    """Static metadata must be named and reviewed as compatibility policy."""
    with pytest.raises(TypeError, match="compatibility_pins"):

        class _AmbiguousPinnedUpdater(_ConfiguredDownloadUpdater):
            source_pins: ClassVar[dict[str, str]] = {"toolVersion": "1.0.0"}


@pytest.mark.parametrize(
    "namespace",
    [
        {
            "compatibility_pins": [],
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {},
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {1: "1.0.0"},
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {"toolVersion": 1},
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {"": "1.0.0"},
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {"  ": "1.0.0"},
            "compatibility_pin_rationale": "test",
        },
        {
            "compatibility_pins": {"toolVersion": "  "},
            "compatibility_pin_rationale": "test",
        },
        {"compatibility_pins": {"toolVersion": "1.0.0"}},
        {
            "compatibility_pins": {"toolVersion": "1.0.0"},
            "compatibility_pin_rationale": "",
        },
    ],
)
def test_updater_rejects_invalid_compatibility_policy(
    namespace: dict[str, object],
) -> None:
    """Compatibility metadata must be string-valued and explain its boundary."""
    with pytest.raises(TypeError, match="compatibility"):
        type("_InvalidCompatibilityUpdater", (_ConfiguredDownloadUpdater,), namespace)


@pytest.mark.parametrize(
    "namespace",
    [
        {
            "compatibility_source_digests": [],
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"": {"source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {1: {"source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": []},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"/source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {".": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"..": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"src/../source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"src/./source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"src//source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"source.rs?raw=1": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {
                "runtime": {"source.rs#fragment": "a" * 64}
            },
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {r"src\source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {1: "a" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"source.rs": 1}},
            "compatibility_source_digest_rationale": "test",
        },
        {
            "compatibility_source_digests": {"runtime": {"source.rs": "A" * 64}},
            "compatibility_source_digest_rationale": "test",
        },
        {"compatibility_source_digests": {"runtime": {"source.rs": "a" * 64}}},
        {
            "compatibility_source_digests": {"runtime": {"source.rs": "a" * 64}},
            "compatibility_source_digest_rationale": "",
        },
    ],
)
def test_updater_rejects_invalid_compatibility_source_digest_policy(
    namespace: dict[str, object],
) -> None:
    """Reviewed byte contracts require typed names, paths, digests, and rationale."""
    with pytest.raises(TypeError, match="compatibility_source"):
        type("_InvalidDigestUpdater", (_ConfiguredDownloadUpdater,), namespace)


def test_updater_exposes_validated_compatibility_contract_members() -> None:
    """Consumers resolve reviewed scalar and byte constraints through base policy."""

    class _CompatibilityUpdater(_ConfiguredDownloadUpdater):
        compatibility_pin_rationale = "exercise scalar policy lookup"
        compatibility_pins: ClassVar[dict[str, str]] = {"runtimeVersion": "2.0.0"}
        compatibility_source_digest_rationale = "exercise source-byte policy lookup"
        compatibility_source_digests: ClassVar[dict[str, dict[str, str]]] = {
            "runtime": {"source.rs": "a" * 64}
        }

    assert _CompatibilityUpdater.get_compatibility_pin("runtimeVersion") == "2.0.0"
    assert _CompatibilityUpdater.get_compatibility_source_digests() == {
        "runtime": {"source.rs": "a" * 64}
    }
    assert _ConfiguredDownloadUpdater.get_compatibility_source_digests() is None
    assert _CompatibilityUpdater.get_compatibility_source_digest_contract(
        "runtime"
    ) == {"source.rs": "a" * 64}
    with pytest.raises(RuntimeError, match="missing compatibility pin"):
        _CompatibilityUpdater.get_compatibility_pin("missing")
    with pytest.raises(RuntimeError, match="missing compatibility pin"):
        _ConfiguredDownloadUpdater.get_compatibility_pin("missing")
    with pytest.raises(RuntimeError, match="missing source digest contract"):
        _CompatibilityUpdater.get_compatibility_source_digest_contract("missing")
    with pytest.raises(RuntimeError, match="missing source digest contract"):
        _ConfiguredDownloadUpdater.get_compatibility_source_digest_contract("missing")


def test_download_updater_applies_explicit_retry_and_timeout_config() -> None:
    """The config bound at the updater seam governs URL prefetch subprocesses."""
    calls: list[float] = []

    async def _prefetch(
        _url: str,
        *,
        name: str | None = None,
        command_timeout: float,
    ) -> str:
        _ = name
        calls.append(command_timeout)
        if len(calls) == 1:
            raise NixCommandError(
                NixCommandResultData(
                    args=["nix-prefetch-url"],
                    returncode=1,
                    stdout="",
                    stderr="HTTP error 503",
                ),
                "transient prefetch failure",
            )
        return "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo="

    async def _run() -> list[UpdateEvent]:
        config = resolve_config(
            retries=2,
            retry_backoff=0,
            subprocess_timeout=17,
        )
        updater = _ConfiguredDownloadUpdater(config=config)
        with patch("lib.update.process.libnix_prefetch_url", _prefetch):
            async with aiohttp.ClientSession() as session:
                return [event async for event in updater.update_stream(None, session)]

    events = asyncio.run(_run())

    assert calls == [17, 17]
    assert events[-1].kind is UpdateEventKind.RESULT


def test_generic_updater_treats_changed_source_pins_as_stale() -> None:
    """Download-only updaters cannot skip a pin-only transaction."""

    class _PinnedDownloadUpdater(_ConfiguredDownloadUpdater):
        compatibility_pin_rationale = "exercise reviewed static compatibility drift"
        compatibility_pins: ClassVar[dict[str, str]] = {"electronVersion": "41.0.0"}

    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": {},
        "pins": {"electronVersion": "40.9.3"},
    })

    assert (
        asyncio.run(
            _PinnedDownloadUpdater()._is_latest(
                current,
                VersionInfo(version="1.0.0"),
            ),
        )
        is False
    )


def test_download_updater_uses_urls_for_same_version_freshness() -> None:
    """A changed artifact URL must rehash without penalizing an exact match."""

    class _ResolvedDownloadUpdater(_ConfiguredDownloadUpdater):
        def get_download_url(self, platform: str, info: VersionInfo) -> str:
            _ = platform
            return require_metadata_str(
                info.metadata,
                "url",
                context="test download metadata",
            )

    updater = _ResolvedDownloadUpdater()
    candidate_url = "https://example.com/replacement.tar.gz"
    info = VersionInfo(version="1.0.0", metadata={"url": candidate_url})
    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "hashes": {},
        "urls": {"aarch64-darwin": "https://example.com/original.tar.gz"},
    })

    assert asyncio.run(updater._is_latest(current, info)) is False
    exact = current.model_copy(
        update={"urls": {"aarch64-darwin": candidate_url}},
    )
    assert asyncio.run(updater._is_latest(exact, info)) is True


class _FakeHashEntryUpdater(HashEntryUpdater):
    name = "fake-hash-updater"

    def __init__(self, *, version: str) -> None:
        super().__init__()
        self._version = version
        self.fetch_hashes_called = False

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(
            version=object.__getattribute__(self, "_version"), metadata={}
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Run this test case."""
        _ = info
        _ = session
        self.fetch_hashes_called = True
        entries: list[HashEntry] = [
            HashEntry.create(
                hash_type="sha256",
                hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
            )
        ]
        yield UpdateEvent.value(
            self.name,
            entries,
        )


def test_hash_entry_updater_build_result_preserves_version() -> None:
    """Hash-only updaters should persist version for latest checks."""
    updater = _FakeHashEntryUpdater(version="v1.2.3")
    info = VersionInfo(version="v1.2.3", metadata={})
    hashes = [
        HashEntry.create(
            hash_type="sha256",
            hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
        ),
    ]

    result = updater.build_result(info, hashes)

    assert result.version == "v1.2.3"


def test_hash_entry_updater_recomputes_before_confirming_equivalence() -> None:
    """Generic hash-entry updaters must recompute before declaring no change."""

    async def _collect_events() -> list[UpdateEvent]:
        updater = _FakeHashEntryUpdater(version="v9.9.9")
        current = SourceEntry(
            version="v9.9.9",
            hashes=HashCollection(
                entries=[
                    HashEntry.create(
                        hash_type="sha256",
                        hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                    ),
                ],
            ),
        )

        async with aiohttp.ClientSession() as session:
            events = [event async for event in updater.update_stream(current, session)]

        assert updater.fetch_hashes_called is True
        return events

    events = asyncio.run(_collect_events())
    status_messages = [
        event.message for event in events if event.kind == UpdateEventKind.STATUS
    ]

    assert "Up to date" in status_messages


# ---------------------------------------------------------------------------
# FlakeInputHashUpdater fingerprint-based staleness tests
# ---------------------------------------------------------------------------


def test_flake_input_hash_updater_persists_declared_source_pins() -> None:
    """Simple dependency locks are written through the updater transaction."""

    class _PinnedUpdater(FlakeInputHashUpdater):
        name = "pinned-package"
        hash_type = "vendorHash"
        compatibility_pin_rationale = "exercise reviewed static compatibility output"
        compatibility_pins: ClassVar[dict[str, str]] = {
            "electronVersion": "40.9.3",
            "packageManagerVersion": "1.0.0",
        }

    result = _PinnedUpdater().build_result(
        VersionInfo(version="1.2.3"),
        {"aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
    )

    assert result.pins == _PinnedUpdater.compatibility_pins


def test_flake_backed_metadata_builders_persist_declared_source_pins() -> None:
    """Every flake-backed result builder must honor the public pin contract."""

    class _PinnedMetadataUpdater(FlakeInputMetadataUpdater):
        name = "pinned-metadata"
        compatibility_pin_rationale = "exercise metadata updater compatibility output"
        compatibility_pins: ClassVar[dict[str, str]] = {"toolVersion": "1.0.0"}

    class _PinnedDenoManifestUpdater(DenoManifestUpdater):
        name = "pinned-deno-manifest"
        compatibility_pin_rationale = "exercise manifest updater compatibility output"
        compatibility_pins: ClassVar[dict[str, str]] = {"toolVersion": "1.0.0"}

    class _PinnedUvLockUpdater(UvLockUpdater):
        name = "pinned-uv-lock"
        compatibility_pin_rationale = "exercise lock updater compatibility output"
        compatibility_pins: ClassVar[dict[str, str]] = {"toolVersion": "1.0.0"}

    for updater_type in (
        _PinnedMetadataUpdater,
        _PinnedDenoManifestUpdater,
        _PinnedUvLockUpdater,
    ):
        updater = updater_type()
        result = updater.build_result(VersionInfo(version="1.2.3"), [])
        assert result.pins == {"toolVersion": "1.0.0"}


def test_flake_input_hash_updater_treats_changed_source_pins_as_stale() -> None:
    """A pin-only updater change must still enter the transactional write path."""

    class _PinnedUpdater(FlakeInputHashUpdater):
        name = "pinned-package"
        hash_type = "vendorHash"
        compatibility_pin_rationale = "exercise reviewed static compatibility drift"
        compatibility_pins: ClassVar[dict[str, str]] = {"electronVersion": "41.0.0"}

        async def _compute_drv_fingerprint(
            self,
            source_override: SourceEntry | None = None,
        ) -> str:
            _ = source_override
            return "unchanged-drv"

    current = SourceEntry.model_validate({
        "version": "1.2.3",
        "drvHash": "unchanged-drv",
        "hashes": [],
        "pins": {"electronVersion": "40.9.3"},
    })

    assert (
        asyncio.run(
            _PinnedUpdater()._is_latest(
                current,
                VersionInfo(version="1.2.3"),
            ),
        )
        is False
    )


def test_flake_input_hash_updater_treats_changed_electron_runtime_as_stale() -> None:
    """A manifest-selected runtime change must invalidate a branch-named source."""

    class _ElectronUpdater(FlakeInputHashUpdater):
        name = "electron-package"
        hash_type = "nodeModulesHash"

        def build_result(
            self,
            info: VersionInfo,
            hashes: SourceHashes,
        ) -> SourceEntry:
            return (
                super()
                .build_result(info, hashes)
                .model_copy(update={"electron_version": "42.0.0"})
            )

    current = SourceEntry(
        version="main",
        drv_hash="unchanged-drv",
        electron_version="41.0.0",
        hashes=[],
    )

    assert (
        asyncio.run(
            _ElectronUpdater()._is_latest(
                current,
                VersionInfo(version="main"),
            )
        )
        is False
    )


def test_flake_input_hash_updater_dynamic_pin_change_converges_in_one_run() -> None:
    """Hash and fingerprint probes must evaluate release-derived pin metadata."""

    class _PinnedPackageUpdater(FlakeInputHashUpdater):
        name = "t3code-desktop"
        hash_type = "nodeModulesHash"

        def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
            return {
                "electronBuilderVersion": require_metadata_str(
                    info.metadata,
                    "electronBuilderVersion",
                    context="test release metadata",
                )
            }

        async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
            _ = session
            return VersionInfo(
                version="1.0.0",
                metadata={"electronBuilderVersion": "26.15.7"},
            )

    expression_calls: list[dict[str, object]] = []
    fixed_hash_calls = 0

    def _package_expression(
        package: str,
        attr_path: str,
        *,
        system: str | None = None,
        source_overrides: dict[str, SourceEntry] | None = None,
        fake_hashes: bool | None = None,
    ) -> str:
        expression_calls.append({
            "attr_path": attr_path,
            "fake_hashes": fake_hashes,
            "package": package,
            "source_overrides": source_overrides,
            "system": system,
        })
        return "candidate-package-expression"

    async def _fixed_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> EventStream:
        nonlocal fixed_hash_calls
        fixed_hash_calls += 1
        assert source == "t3code-desktop"
        assert expr == "candidate-package-expression"
        _ = config
        yield UpdateEvent.value(
            source, "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        )

    async def _fingerprint(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> str:
        assert source == "t3code-desktop"
        assert expr == "candidate-package-expression"
        _ = config
        return "candidate-drv"

    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "input": "t3code-desktop",
        "drvHash": "old-drv",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            }
        ],
        "pins": {"electronBuilderVersion": "26.8.1"},
    })

    async def _run() -> tuple[list[UpdateEvent], list[UpdateEvent]]:
        updater = _PinnedPackageUpdater()
        async with aiohttp.ClientSession() as session:
            first = [event async for event in updater.update_stream(current, session)]
            candidate = first[-1].payload
            assert isinstance(candidate, SourceEntry)
            second = [
                event async for event in updater.update_stream(candidate, session)
            ]
        return first, second

    with (
        patch(
            "lib.update.updaters.flake_backed._build_package_path_attr_expr",
            _package_expression,
        ),
        patch("lib.update.nix.compute_fixed_output_hash", _fixed_hash),
        patch("lib.update.nix.compute_expr_drv_fingerprint", _fingerprint),
    ):
        first_events, second_events = asyncio.run(_run())

    first_result = first_events[-1].payload
    assert isinstance(first_result, SourceEntry)
    expected_pins = {"electronBuilderVersion": "26.15.7"}
    assert first_result.pins == expected_pins
    assert first_result.drv_hash == "candidate-drv"
    assert fixed_hash_calls == 1
    assert second_events[-1] == UpdateEvent.result("t3code-desktop")
    assert len(expression_calls) == 3
    for call in expression_calls:
        assert call["fake_hashes"] is True
        source_overrides = call["source_overrides"]
        assert isinstance(source_overrides, dict)
        override = source_overrides["t3code-desktop"]
        assert override.pins == expected_pins
    assert expression_calls[0]["source_overrides"] == {
        "t3code-desktop": _PinnedPackageUpdater().build_result(
            VersionInfo(
                version="1.0.0",
                metadata={"electronBuilderVersion": "26.15.7"},
            ),
            [],
        )
    }
    assert expression_calls[1] == expression_calls[2]


def test_flake_input_hash_updater_overlay_probe_receives_candidate_pins() -> None:
    """Pin-bearing overlay probes must evaluate the complete candidate source."""

    class _PinnedOverlayUpdater(FlakeInputHashUpdater):
        name = "pinned-overlay-candidate"
        hash_type = "vendorHash"
        compatibility_pin_rationale = "exercise overlay compatibility projection"
        compatibility_pins: ClassVar[dict[str, str]] = {"toolVersion": "2.0.0"}

    captured: dict[str, object] = {}

    async def _overlay_hash(
        source: str,
        *,
        system: str | None = None,
        config: object = None,
        source_overrides: dict[str, SourceEntry] | None = None,
        fake_hashes: bool | None = None,
    ) -> EventStream:
        captured.update({
            "config": config,
            "fake_hashes": fake_hashes,
            "source": source,
            "source_overrides": source_overrides,
            "system": system,
        })
        yield UpdateEvent.value(source, "sha256-overlay")

    updater = _PinnedOverlayUpdater()
    info = VersionInfo(version="1.0.0")

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in updater._compute_hash_for_system(
                info,
                system="aarch64-darwin",
            )
        ]

    with (
        patch("lib.update.paths.package_file_for", return_value=None),
        patch("lib.update.nix.compute_overlay_hash", _overlay_hash),
    ):
        events = asyncio.run(_collect())

    assert events == [UpdateEvent.value(updater.name, "sha256-overlay")]
    assert captured == {
        "config": updater.config,
        "fake_hashes": True,
        "source": updater.name,
        "source_overrides": {updater.name: updater.build_result(info, [])},
        "system": "aarch64-darwin",
    }


def test_deno_hash_updater_passes_candidate_pins_to_platform_probes() -> None:
    """Deno probes must preserve old platform hashes but use the new pin set."""

    class _PinnedDenoUpdater(DenoDepsHashUpdater):
        name = "pinned-deno"
        input_name = "pinned-deno-input"
        compatibility_pin_rationale = "exercise Deno compatibility projection"
        compatibility_pins: ClassVar[dict[str, str]] = {"runtimeVersion": "2.0.0"}

    captured: dict[str, object] = {}

    async def _compute_deno_hash(
        source: str,
        input_name: str,
        *,
        native_only: bool = False,
        config: object = None,
        source_override: SourceEntry | None = None,
    ) -> EventStream:
        captured.update({
            "config": config,
            "input_name": input_name,
            "native_only": native_only,
            "source": source,
            "source_override": source_override,
        })
        yield UpdateEvent.value(
            source,
            {"aarch64-darwin": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="},
        )

    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "input": "pinned-deno-input",
        "hashes": [
            {
                "hashType": "denoDepsHash",
                "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "platform": "aarch64-darwin",
            }
        ],
        "pins": {"runtimeVersion": "1.0.0"},
    })
    updater = _PinnedDenoUpdater()

    async def _collect() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in updater.fetch_hashes(
                    VersionInfo(version="1.0.0"),
                    session,
                    context=current,
                )
            ]

    with patch(
        "lib.update.nix_deno.compute_deno_deps_hash",
        _compute_deno_hash,
    ):
        events = asyncio.run(_collect())

    assert events[-1].kind is UpdateEventKind.VALUE
    source_override = captured["source_override"]
    assert isinstance(source_override, SourceEntry)
    assert source_override.version == "1.0.0"
    assert source_override.input == "pinned-deno-input"
    assert source_override.pins == _PinnedDenoUpdater.compatibility_pins
    assert source_override.hashes.equivalent_to(current.hashes)
    assert updater._candidate_source_override(
        VersionInfo(version="2.0.0"),
        None,
    ) == updater.build_result(VersionInfo(version="2.0.0"), [])
    mapping_current = SourceEntry(
        version="1.0.0",
        input="pinned-deno-input",
        hashes={"aarch64-darwin": "sha256-mapped"},
        pins={"runtimeVersion": "1.0.0"},
    )
    assert updater._candidate_source_override(
        VersionInfo(version="2.0.0"),
        mapping_current,
    ) == updater.build_result(
        VersionInfo(version="2.0.0"),
        {"aarch64-darwin": "sha256-mapped"},
    )


def test_source_then_overlay_hashes_carries_candidate_source_pins() -> None:
    """Second-pass dependency probes must see updater-owned package locks."""
    captured_overrides: list[dict[str, SourceEntry] | None] = []
    hash_calls = 0

    def _overlay_expression(
        source: str,
        *,
        system: str | None = None,
        repo_root: str | None = None,
        source_overrides: dict[str, SourceEntry] | None = None,
        fake_hashes: bool | None = None,
    ) -> str:
        assert source == "pinned-two-pass"
        _ = (system, repo_root, fake_hashes)
        captured_overrides.append(source_overrides)
        return "dependency-expression"

    async def _fixed_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> EventStream:
        nonlocal hash_calls
        hash_calls += 1
        assert source == "pinned-two-pass"
        assert expr in {"source-expression", "dependency-expression"}
        _ = config
        value = (
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            if hash_calls == 1
            else "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
        )
        yield UpdateEvent.value(source, value)

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in stream_source_then_overlay_hashes(
                "pinned-two-pass",
                version="1.0.0",
                commit="a" * 40,
                src_expr="source-expression",
                dependency_hash_type="npmDepsHash",
                source_pins={"electronVersion": "42.0.1"},
            )
        ]

    with (
        patch("lib.update.updaters.core._build_overlay_expr", _overlay_expression),
        patch("lib.update.nix.compute_fixed_output_hash", _fixed_hash),
    ):
        events = asyncio.run(_collect())

    assert events[-1].kind is UpdateEventKind.VALUE
    assert len(captured_overrides) == 1
    source_overrides = captured_overrides[0]
    assert source_overrides is not None
    assert source_overrides["pinned-two-pass"].pins == {"electronVersion": "42.0.1"}
    assert source_overrides["pinned-two-pass"].commit == "a" * 40


def test_package_flake_input_updater_hashes_discovered_package_expression() -> None:
    """Package-owned hash updaters should not require an overlay export."""

    class _PackageUpdater(FlakeInputHashUpdater):
        name = "anthropic-cli"
        hash_type = "vendorHash"

    captured: dict[str, object] = {}

    async def _compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> EventStream:
        captured.update({"source": source, "expr": expr, "config": config})
        yield UpdateEvent.value(source, "sha256-package")

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in updater._compute_hash_for_system(
                VersionInfo(version="1.0.0"),
                system="aarch64-darwin",
            )
        ]

    updater = _PackageUpdater()
    with (
        patch(
            "lib.update.nix.compute_fixed_output_hash",
            _compute_fixed_output_hash,
        ),
        patch(
            "lib.update.nix.compute_overlay_hash",
            side_effect=AssertionError("package updater used overlay route"),
        ),
    ):
        events = asyncio.run(_collect())

    assert events == [UpdateEvent.value("anthropic-cli", "sha256-package")]
    assert captured["source"] == "anthropic-cli"
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr(
            "anthropic-cli",
            "",
            system="aarch64-darwin",
        ),
    )


def test_package_flake_input_updater_fingerprints_discovered_package() -> None:
    """Package-owned staleness checks should use the package derivation."""

    class _PackageUpdater(FlakeInputHashUpdater):
        name = "anthropic-cli"
        hash_type = "vendorHash"

    captured: dict[str, object] = {}

    async def _compute_expr_drv_fingerprint(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> str:
        captured.update({"source": source, "expr": expr, "config": config})
        return "package-drv"

    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "drvHash": "package-drv",
        "hashes": [],
    })
    updater = _PackageUpdater()
    with (
        patch(
            "lib.update.nix.compute_expr_drv_fingerprint",
            _compute_expr_drv_fingerprint,
        ),
        patch(
            "lib.update.nix.compute_drv_fingerprint",
            side_effect=AssertionError("package updater used overlay fingerprint"),
        ),
    ):
        is_latest = asyncio.run(
            updater._is_latest(current, VersionInfo(version="1.0.0"))
        )

    assert is_latest is True
    assert captured["source"] == "anthropic-cli"
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr("anthropic-cli", ""),
    )
