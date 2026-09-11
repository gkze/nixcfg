"""Completeness and scope of reusable dependency-hash fingerprints."""

import asyncio
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.config import resolve_config
from lib.update.constants import FAKE_HASH
from lib.update.events import EventSink, UpdateEvent, UpdateEventKind, ignore_event
from lib.update.nix import PreparedProbe
from lib.update.updaters import UpdateContext, Updater, VersionInfo
from lib.update.updaters.flake_backed import DenoDepsHashUpdater, FlakeInputHashUpdater

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceHashes

_NATIVE = "aarch64-darwin"
_FOREIGN = "x86_64-linux"
_HASH = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
_NEW_HASH = "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC="


class _PlatformUpdater(FlakeInputHashUpdater):
    name = "fingerprint-test"
    input_name = "test-input"
    hash_type = "nodeModulesHash"
    platform_specific = True

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        _ = (session, context)
        return VersionInfo(version="1.0.0", metadata={})


def _current(hashes: dict[str, str], fingerprint: str) -> SourceEntry:
    return SourceEntry(
        version="1.0.0",
        input="test-input",
        drv_hash=fingerprint,
        hashes=HashCollection(
            entries=[
                HashEntry.create("nodeModulesHash", value, platform=platform)
                for platform, value in hashes.items()
            ]
        ),
    )


def _fingerprint_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, str], list[str | None]]:
    fingerprints = {_NATIVE: "native-drv", _FOREIGN: "foreign-drv"}
    calls: list[str | None] = []

    async def compute(
        _source: str, *, system: str | None = None, **_kwargs: object
    ) -> str:
        calls.append(system)
        return fingerprints[system or _NATIVE]

    monkeypatch.setattr("lib.update.nix.get_current_nix_platform", lambda: _NATIVE)
    monkeypatch.setattr("lib.update.nix.compute_drv_fingerprint", compute)

    async def prepare(
        _source: str, expressions: dict[str, str], **_kwargs: object
    ) -> dict[str, PreparedProbe]:
        return {
            system: PreparedProbe(
                "/nix/store/probe.drv",
                await compute(_source, system=system or None),
                expression,
            )
            for system, expression in expressions.items()
        }

    monkeypatch.setattr("lib.update.nix.prepare_fixed_output_probes", prepare)
    return fingerprints, calls


def test_full_fingerprint_detects_foreign_drift_and_scope_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unchanged native derivation cannot certify a changed foreign input."""
    fingerprints, _calls = _fingerprint_boundary(monkeypatch)
    updater = _PlatformUpdater(
        config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN))
    )
    certificate = asyncio.run(updater._compute_drv_fingerprint())
    current = _current({_NATIVE: _HASH, _FOREIGN: _HASH}, certificate)
    info = VersionInfo(version="1.0.0", metadata={})
    assert asyncio.run(updater._is_latest(UpdateContext(current=current), info))

    fingerprints[_FOREIGN] = "changed-foreign-drv"
    assert not asyncio.run(updater._is_latest(UpdateContext(current=current), info))
    fingerprints[_FOREIGN] = "foreign-drv"

    reordered = _PlatformUpdater(
        config=resolve_config(hash_build_platforms=(_FOREIGN, _NATIVE))
    )
    assert asyncio.run(reordered._compute_drv_fingerprint()) == certificate
    single = _PlatformUpdater(config=resolve_config(hash_build_platforms=(_NATIVE,)))
    native_certificate = asyncio.run(single._compute_drv_fingerprint())
    assert native_certificate == "native-drv"
    assert native_certificate != certificate
    legacy = current.model_copy(update={"drv_hash": native_certificate})
    assert not asyncio.run(updater._is_latest(UpdateContext(current=legacy), info))


def test_unavailable_foreign_fingerprint_cannot_prove_freshness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed foreign evaluation must invalidate the shortcut, not certify it."""
    calls: list[str | None] = []

    async def prepare(
        _source: str, expressions: dict[str, str], **_kwargs: object
    ) -> dict[str, PreparedProbe]:
        calls.extend(expressions)
        raise RuntimeError("foreign derivation unavailable")

    monkeypatch.setattr("lib.update.nix.get_current_nix_platform", lambda: _NATIVE)
    monkeypatch.setattr("lib.update.nix.prepare_fixed_output_probes", prepare)
    updater = _PlatformUpdater(
        config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN))
    )
    context = UpdateContext(
        current=_current({_NATIVE: _HASH, _FOREIGN: _HASH}, "old-full-certificate")
    )
    assert not asyncio.run(
        updater._is_latest(context, VersionInfo(version="1.0.0", metadata={}))
    )
    assert set(calls) == {_NATIVE, _FOREIGN}
    assert context.drv_fingerprint is None


@pytest.mark.parametrize("foreign_hash", [None, FAKE_HASH, _NEW_HASH])
def test_full_fingerprint_requires_real_hash_for_every_target(
    monkeypatch: pytest.MonkeyPatch, foreign_hash: str | None
) -> None:
    """Missing and configured placeholder hashes invalidate a cached certificate."""
    _fingerprints, calls = _fingerprint_boundary(monkeypatch)
    updater = _PlatformUpdater(
        config=resolve_config(
            hash_build_platforms=(_NATIVE, _FOREIGN), fake_hash=_NEW_HASH
        )
    )
    certificate = asyncio.run(updater._compute_drv_fingerprint())
    calls.clear()
    hashes = {_NATIVE: _HASH}
    if foreign_hash is not None:
        hashes[_FOREIGN] = foreign_hash
    assert not asyncio.run(
        updater._is_latest(
            UpdateContext(current=_current(hashes, certificate)),
            VersionInfo(version="1.0.0", metadata={}),
        )
    )
    assert calls == []


@pytest.mark.parametrize("hash_value", [None, FAKE_HASH, _HASH])
def test_global_fingerprint_requires_its_own_real_hash(
    monkeypatch: pytest.MonkeyPatch, hash_value: str | None
) -> None:
    """A global dependency hash must exist even when its derivation is unchanged."""
    _fingerprint_boundary(monkeypatch)
    updater = _PlatformUpdater()
    updater.platform_specific = False
    hashes = (
        [] if hash_value is None else [HashEntry.create("nodeModulesHash", hash_value)]
    )
    current = _current({}, "native-drv").model_copy(
        update={"hashes": HashCollection(entries=hashes)}
    )
    assert asyncio.run(
        updater._is_latest(
            UpdateContext(current=current), VersionInfo(version="1.0.0", metadata={})
        )
    ) is (hash_value == _HASH)


def test_native_only_preserves_full_certificate_without_foreign_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial native work retains the earlier full certificate and foreign hash."""
    _fingerprints, fingerprint_calls = _fingerprint_boundary(monkeypatch)
    updater = _PlatformUpdater(
        config=resolve_config(hash_build_platforms=(_NATIVE, _FOREIGN))
    )
    certificate = asyncio.run(updater._compute_drv_fingerprint())
    current = _current({_NATIVE: _HASH, _FOREIGN: _HASH}, certificate)
    fingerprint_calls.clear()
    updater.native_only = True
    hash_calls: list[str | None] = []

    async def compute_hash(
        _source: str, probe: PreparedProbe, **_kwargs: object
    ) -> str:
        hash_calls.append(_NATIVE if probe.fingerprint == "native-drv" else _FOREIGN)
        return _NEW_HASH

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", compute_hash)

    async def run() -> SourceEntry | None:
        async with aiohttp.ClientSession() as session:
            return await updater.update_stream(current, session)

    result = asyncio.run(run())
    assert result is not None
    assert result.drv_hash == certificate
    assert hash_calls == [_NATIVE]
    assert fingerprint_calls == [_NATIVE]
    merged = current.merge_native_update(result)
    assert {(entry.platform, entry.hash) for entry in merged.hashes.entries or ()} == {
        (_NATIVE, _NEW_HASH),
        (_FOREIGN, _HASH),
    }


def test_deno_alias_and_supported_scope_match_hash_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deno hash computation uses the same filtered alias scope as fingerprints."""
    _fingerprints, fingerprint_calls = _fingerprint_boundary(monkeypatch)

    class _Deno(DenoDepsHashUpdater):
        name = "fingerprint-deno-test"
        supported_platforms = (_NATIVE, _FOREIGN)

    updater = _Deno(
        config=resolve_config(deno_platforms=f"{_NATIVE},aarch64-linux,{_FOREIGN}")
    )
    hash_calls: list[str] = []

    async def compute_hash(
        _source: str, _input: str, platform: str, **_kwargs: object
    ) -> tuple[str, str]:
        hash_calls.append(platform)
        return platform, _HASH

    monkeypatch.setattr("lib.update.nix_deno.get_current_nix_platform", lambda: _NATIVE)
    monkeypatch.setattr(
        "lib.update.nix_deno._compute_deno_deps_hash_for_platform", compute_hash
    )
    asyncio.run(updater._compute_drv_fingerprint())
    hashes = asyncio.run(
        updater._compute_platform_hashes(
            VersionInfo(version="1.0.0", metadata={}),
            source_override=SourceEntry(hashes={}),
        )
    )
    assert set(fingerprint_calls) == set(hash_calls) == {_NATIVE, _FOREIGN}
    assert hashes == {_NATIVE: _HASH, _FOREIGN: _HASH}


def test_partial_subclass_cannot_change_global_identity() -> None:
    """The generic updater contract still rejects incoherent partial results."""

    class _Partial(Updater):
        name = "partial-test"

        async def fetch_latest(
            self, session: aiohttp.ClientSession, *, context: UpdateContext
        ) -> VersionInfo:
            _ = (session, context)
            return VersionInfo(version="2.0.0", metadata={})

        async def fetch_hashes(
            self,
            info: VersionInfo,
            session: aiohttp.ClientSession,
            *,
            context: UpdateContext,
            emit: EventSink = ignore_event,
        ) -> SourceHashes:
            _ = (info, session, emit)
            context.hashes_fully_computed = False
            return [HashEntry.create("sha256", _NEW_HASH, platform=_NATIVE)]

    emitted: list[UpdateEvent] = []

    async def emit(event: UpdateEvent) -> None:
        emitted.append(event)

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            await _Partial().update_stream(
                SourceEntry(version="1.0.0", hashes={}), session, emit=emit
            )

    with pytest.raises(
        RuntimeError, match=r"Cannot apply partial update.*changed \(version\)"
    ):
        asyncio.run(run())
    assert not any(event.kind is UpdateEventKind.RESULT for event in emitted)
