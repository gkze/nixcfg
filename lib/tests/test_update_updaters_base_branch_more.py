"""Additional branch-focused tests for updater base internals."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import aiohttp
import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import SourceEntry
from lib.update.events import UpdateEvent
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    DenoDepsHashUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    UpdateContext,
    Updater,
    VersionInfo,
    _call_with_optional_context,
    _compute_url_hashes,
    _convert_nix_hash_to_sri,
    _ensure_str_mapping,
    _updater_sourcefile,
)
from lib.update.updaters.dependencies import updater_dependencies
from lib.update.updaters.metadata import FlakeInputMetadata

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _run[T](awaitable: object) -> T:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


async def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    return [event async for event in stream]


class _YieldingUpdater(Updater):
    name = "yielding"

    async def fetch_latest(self, session: object) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0", metadata={})

    async def fetch_hashes(
        self, info: VersionInfo, session: object
    ) -> AsyncIterator[UpdateEvent]:
        _ = (info, session)
        yield UpdateEvent.status(self.name, "hashing")
        yield UpdateEvent.value(self.name, {"x86_64-linux": HASH_A})

    async def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        _ = (current, info)
        return False

    async def _finalize_result(self, result: SourceEntry) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status(self.name, "finalizing")
        yield UpdateEvent.value(self.name, result)


class _DownloadNoBase(DownloadHashUpdater):
    name = "download-no-base"
    BASE_URL = ""
    PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "https://example.com/a"}

    async def fetch_latest(self, session: object) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0", metadata={})


class _DefaultFlake(FlakeInputHashUpdater):
    name = "default-flake"
    input_name = "default-flake"
    hash_type = "sha256"


class _DefaultDeno(DenoDepsHashUpdater):
    name = "default-deno"
    input_name = "default-deno"

    async def fetch_latest(self, session: object) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0", metadata={})


class _Manifest(DenoDepsHashUpdater):
    name = "manifest-like"
    input_name = "manifest-like"

    async def fetch_latest(self, session: object) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0", metadata={})


def test_helper_aliases_and_type_guard_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute thin wrapper aliases and strict mapping validators."""

    async def _url_hashes(source_name: str, urls: object) -> AsyncIterator[UpdateEvent]:
        _ = urls
        yield UpdateEvent.status(source_name, "url-hashes")

    async def _convert(source_name: str, nix_hash: str) -> AsyncIterator[UpdateEvent]:
        _ = nix_hash
        yield UpdateEvent.status(source_name, "convert")

    monkeypatch.setattr(
        "lib.update.updaters.base.update_process.compute_url_hashes", _url_hashes
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.update_process.convert_nix_hash_to_sri", _convert
    )

    events = _run(_collect(_compute_url_hashes("demo", ["https://x"])))
    assert events[0].message == "url-hashes"
    events = _run(_collect(_convert_nix_hash_to_sri("demo", "deadbeef")))
    assert events[0].message == "convert"

    with pytest.raises(TypeError, match="Expected dict for platform/hash mapping"):
        _ = _ensure_str_mapping("bad")
    with pytest.raises(TypeError, match="Expected platform/hash string mapping"):
        _ = _ensure_str_mapping({"ok": 1})


def test_dependency_adapter_uses_public_base_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep adapter calls compatible with monkeypatches on the base facade."""

    async def _url_hashes(source_name: str, urls: object) -> AsyncIterator[UpdateEvent]:
        _ = urls
        yield UpdateEvent.status(source_name, "adapter-url-hashes")

    monkeypatch.setattr("lib.update.updaters.base.compute_url_hashes", _url_hashes)

    events = _run(_collect(updater_dependencies().compute_url_hashes("demo", [])))
    assert events[0].message == "adapter-url-hashes"


def test_updater_sourcefile_falls_back_to_module_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use module __file__ when inspect.getsourcefile is unavailable."""

    class _FallbackUpdater(Updater):
        name = "fallback-sourcefile"

        async def fetch_latest(self, session: object) -> VersionInfo:
            _ = session
            return VersionInfo(version="1", metadata={})

        async def fetch_hashes(
            self, info: VersionInfo, session: object
        ) -> AsyncIterator[UpdateEvent]:
            _ = (info, session)
            if False:
                yield UpdateEvent.status(self.name, "never")

    monkeypatch.setattr(
        "lib.update.updaters.base.inspect.getsourcefile",
        lambda _cls: (_ for _ in ()).throw(TypeError("no sourcefile")),
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.inspect.getmodule",
        lambda _cls: type("_Module", (), {"__file__": "/tmp/fallback.py"})(),
    )
    assert _updater_sourcefile(_FallbackUpdater) == "/tmp/fallback.py"


def test_unbound_abstract_methods_raise() -> None:
    """Call abstract method bodies directly to cover defensive raises."""

    class _Concrete(Updater):
        name = "concrete"

        async def fetch_latest(self, session: object) -> VersionInfo:
            _ = session
            return VersionInfo(version="1", metadata={})

        async def fetch_hashes(
            self, info: VersionInfo, session: object
        ) -> AsyncIterator[UpdateEvent]:
            _ = (info, session)
            if False:
                yield UpdateEvent.status(self.name, "never")

    updater = _Concrete()

    with pytest.raises(NotImplementedError):
        _ = _run(Updater.fetch_latest(updater, object()))
    with pytest.raises(NotImplementedError):
        _ = _run(
            _collect(
                Updater.fetch_hashes(
                    updater, VersionInfo(version="1", metadata={}), object()
                )
            )
        )

    class _Checksum(ChecksumProvidedUpdater):
        name = "checksum-abstract"
        PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux"}

        async def fetch_latest(self, session: object) -> VersionInfo:
            _ = session
            return VersionInfo(version="1", metadata={})

        async def fetch_checksums(
            self,
            info: VersionInfo,
            session: object,
        ) -> dict[str, str]:
            return await ChecksumProvidedUpdater.fetch_checksums(self, info, session)

    checksum = _Checksum()
    with pytest.raises(NotImplementedError):
        _ = _run(
            checksum.fetch_checksums(VersionInfo(version="1", metadata={}), object())
        )


def test_update_stream_yields_intermediate_events_and_up_to_date_result() -> None:
    """Forward hash/finalize events and hit result-equality no-change path."""
    updater = _YieldingUpdater()
    current = updater.build_result(
        VersionInfo(version="1.0.0", metadata={}),
        {"x86_64-linux": HASH_A},
    )

    async def _run_events() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [event async for event in updater.update_stream(current, session)]

    events = _run(_run_events())
    assert any(event.message == "hashing" for event in events)
    assert any(event.message == "finalizing" for event in events)
    assert any(event.message == "Up to date" for event in events)


def test_download_and_hash_entry_branch_yields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise DownloadHashUpdater no-base URL path and event forwarding."""
    updater = _DownloadNoBase()
    info = VersionInfo(version="1", metadata={})
    assert updater.get_download_url("x86_64-linux", info) == "https://example.com/a"

    async def _hashes(_source_name: str, _urls: object) -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("download-no-base", "computing")
        yield UpdateEvent.value("download-no-base", {"https://example.com/a": HASH_A})

    monkeypatch.setattr("lib.update.updaters.base.compute_url_hashes", _hashes)

    async def _run_events() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [event async for event in updater.fetch_hashes(info, session)]

    events = _run(_run_events())
    assert any(event.message == "computing" for event in events)


def test_flake_updater_default_compute_and_fetch_hash_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover default compute path plus platform and non-platform hash branches."""
    updater = _DefaultFlake()

    async def _compute_overlay_hash(
        source_name: str,
        *,
        system: str | None,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.status(source_name, f"system={system}")
        yield UpdateEvent.value(source_name, HASH_A)

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_overlay_hash", _compute_overlay_hash
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.get_current_nix_platform", lambda: "x86_64-linux"
    )

    # _is_latest current=None branch
    assert (
        _run(updater._is_latest(None, VersionInfo(version="1", metadata={}))) is False
    )

    # default _compute_hash with non-platform-specific system=None
    non_platform = _run(
        _collect(updater._compute_hash(VersionInfo(version="1", metadata={})))
    )
    assert any(event.message == "system=None" for event in non_platform)

    # non-platform fetch_hashes delegates through _emit_single_hash_entry path
    async def _run_non_platform() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in updater.fetch_hashes(
                    VersionInfo(version="1", metadata={}), session
                )
            ]

    non_platform_events = _run(_run_non_platform())
    assert any(event.message == "system=None" for event in non_platform_events)

    class _PlatformFlake(_DefaultFlake):
        platform_specific = True

    plat = _PlatformFlake()
    plat_events = _run(
        _collect(plat._compute_hash(VersionInfo(version="1", metadata={})))
    )
    assert any(event.message == "system=x86_64-linux" for event in plat_events)

    async def _run_platform() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in plat.fetch_hashes(
                    VersionInfo(version="1", metadata={}), session
                )
            ]

    platform_events = _run(_run_platform())
    assert any(event.message == "system=x86_64-linux" for event in platform_events)


def test_deno_deps_default_compute_and_type_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover DenoDeps default compute path and post-parse dict assertion."""
    updater = _DefaultDeno()

    async def _compute_deno(
        source_name: str,
        input_name: str,
        *,
        native_only: bool,
        config: object,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (input_name, native_only, config)
        yield UpdateEvent.status(source_name, "deno-hash")
        yield UpdateEvent.value(source_name, {"x86_64-linux": HASH_A})

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_deno_deps_hash", _compute_deno
    )

    # default _compute_hash body
    body_events = _run(
        _collect(updater._compute_hash(VersionInfo(version="1", metadata={})))
    )
    assert any(event.message == "deno-hash" for event in body_events)

    async def _run_hashes() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in updater.fetch_hashes(
                    VersionInfo(version="1", metadata={}), session
                )
            ]

    hash_events = _run(_run_hashes())
    assert any(event.message == "deno-hash" for event in hash_events)

    monkeypatch.setattr(
        "lib.update.updaters.base.expect_hash_mapping", lambda _payload: ["bad"]
    )
    with pytest.raises(TypeError, match="Expected dict of platform hashes"):
        _run(_run_hashes())


def test_manifest_updater_rejects_non_flake_node_metadata() -> None:
    """Fail when metadata carries an unexpected node object type."""
    from lib.update.updaters.base import DenoManifestUpdater

    class _ManifestUpdater(DenoManifestUpdater):
        name = "manifest-updater"
        input_name = "manifest-updater"

    updater = _ManifestUpdater()
    info = VersionInfo(version="1", metadata={"node": "bad"})

    async def _run_events() -> None:
        async with aiohttp.ClientSession() as session:
            async for _event in updater.fetch_hashes(info, session):
                pass

    with pytest.raises(TypeError, match="Expected flake lock node in metadata"):
        _run(_run_events())


def test_manifest_updater_accepts_flake_node_metadata_and_checks_lock() -> None:
    """Cover metadata path where ``node`` is already a ``FlakeLockNode``."""
    from lib.update.updaters.base import DenoManifestUpdater

    class _ManifestUpdater(DenoManifestUpdater):
        name = "manifest-updater-node"
        input_name = "manifest-updater-node"

    updater = _ManifestUpdater()
    info = VersionInfo(version="1", metadata={"node": FlakeLockNode(locked=None)})

    async def _run_events() -> None:
        async with aiohttp.ClientSession() as session:
            async for _event in updater.fetch_hashes(info, session):
                pass

    with pytest.raises(RuntimeError, match="incomplete lock"):
        _run(_run_events())


def test_optional_context_and_flake_helper_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover optional-context fallbacks and typed flake metadata branches."""
    context = UpdateContext(current=None)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise TypeError("boom")

    with pytest.raises(TypeError, match="boom"):
        _call_with_optional_context(_boom, context=context)

    def _only_args(*_args: object) -> None:
        return None

    with pytest.raises(TypeError, match="extra"):
        _call_with_optional_context(_only_args, context=context, extra=True)

    updater = _DefaultFlake()
    typed_node = FlakeLockNode(locked=None)
    assert (
        updater._resolve_flake_node(
            VersionInfo(version="1", metadata=FlakeInputMetadata(node=typed_node))
        )
        is typed_node
    )
    fallback_node = FlakeLockNode(locked=None)
    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_node",
        lambda _name: fallback_node,
    )
    assert (
        updater._resolve_flake_node(VersionInfo(version="1", metadata=object()))
        is fallback_node
    )
    built = updater.build_result(
        VersionInfo(version="1", metadata={}),
        {"x86_64-linux": HASH_A},
    )
    assert built.input == "default-flake"

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_drv_fingerprint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    finalized = _run(
        _collect(
            updater._finalize_result(
                SourceEntry.model_validate({"version": "1", "hashes": {}}),
                context=UpdateContext(current=None, drv_fingerprint="drv"),
            )
        )
    )
    payload = finalized[-1].payload
    assert isinstance(payload, SourceEntry)
    if not isinstance(payload, SourceEntry):
        raise AssertionError("expected SourceEntry payload")
    assert payload.drv_hash == "drv"
    assert updater._existing_platform_hashes(
        SourceEntry.model_validate({
            "hashes": {"x86_64-linux": HASH_A},
        })
    ) == {"x86_64-linux": HASH_A}
    assert updater._existing_platform_hashes() == {}
    object.__setattr__(updater, "_current_entry", "bad")
    assert updater._existing_platform_hashes() == {}
