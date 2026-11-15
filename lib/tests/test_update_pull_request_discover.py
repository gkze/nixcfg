"""Discovery probes versions and stops before hashing."""

import asyncio

import aiohttp
import pytest

from lib.nix.models.sources import SourceEntry
from lib.update.pull_request_discover import (
    _fetch_ref,
    _fetch_source,
    discover_live,
    discover_offers,
)
from lib.update.pull_request_plan import ProbeFailure, UpdateOffer
from lib.update.refs import FlakeInputRef, RefUpdateResult
from lib.update.updaters.core import Updater
from lib.update.updaters.metadata import VersionInfo


class _Fake(Updater):
    supported_platforms = ("x86_64-linux",)
    latest = "2"
    current_version = "1"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: object
    ) -> VersionInfo:
        _ = (session, context)
        return VersionInfo(version=self.latest)

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession, *, context: object
    ) -> dict[str, str]:
        _ = (info, session, context)
        return {}

    async def _is_latest(self, context: object, info: VersionInfo) -> bool:
        _ = context
        return info.version == self.current_version


class _NotUpdater:
    pass


def test_discover_reports_movement_timeouts_and_errors() -> None:
    async def fetch_source(
        name: str,
        updater_cls: type[object],
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
    ) -> UpdateOffer | ProbeFailure | None:
        _ = (updater_cls, current, session)
        if name == "quiet":
            return None
        if name == "slow":
            await asyncio.sleep(1)
            return None
        if name == "bad":
            raise RuntimeError("https://user:secret@example.test/file?sig=1")
        return UpdateOffer(
            name=name, kind="source", current="1", latest="2", touches_darwin=False
        )

    async def fetch_ref(
        ref: FlakeInputRef, session: aiohttp.ClientSession
    ) -> RefUpdateResult:
        _ = session
        if ref.name == "nixpkgs":
            return RefUpdateResult(name=ref.name, current_ref="old", latest_ref="new")
        if ref.name == "same":
            return RefUpdateResult(name=ref.name, current_ref="v1", latest_ref="v1")
        return RefUpdateResult(
            name=ref.name, current_ref="v1", latest_ref=None, error="missing"
        )

    async def run() -> tuple[tuple[UpdateOffer, ...], tuple[ProbeFailure, ...]]:
        async with aiohttp.ClientSession() as session:
            return await discover_offers(
                updaters={
                    "moved": object,
                    "plain": object,
                    "quiet": object,
                    "slow": object,
                    "bad": object,
                },
                currents={},
                refs=(
                    FlakeInputRef(
                        "nixpkgs", "NixOS", "nixpkgs", "nixos-unstable", "github"
                    ),
                    FlakeInputRef("same", "NixOS", "nixpkgs", "v1", "github"),
                    FlakeInputRef("gone", "NixOS", "nixpkgs", "v1", "github"),
                ),
                session=session,
                security=frozenset({"moved"}),
                main_fix=frozenset({"nixpkgs"}),
                fetch_source=fetch_source,
                fetch_ref=fetch_ref,
                probe_timeout=0.01,
                limit=4,
            )

    offers, failures = asyncio.run(run())
    by_name = {offer.name: offer for offer in offers}
    assert by_name["moved"].security is True
    assert by_name["plain"].security is False
    assert by_name["plain"].main_fix is False
    assert by_name["moved"].latest == "2"
    assert by_name["nixpkgs"].kind == "input"
    assert by_name["nixpkgs"].main_fix is True
    assert "same" not in by_name
    failed = {item.name: item.error for item in failures}
    assert failed["slow"] == "discovery timed out"
    assert "secret" not in failed["bad"]
    assert "sig" not in failed["bad"]
    assert failed["gone"]


def test_fetch_source_uses_the_updater_latest_proof() -> None:
    async def run() -> None:
        async with aiohttp.ClientSession() as session:

            class _Changed(_Fake):
                latest = "2"

            offer = await _fetch_source("demo", _Changed, None, session)
            assert offer is not None
            assert offer.current == ""
            assert offer.latest == "2"
            assert offer.touches_darwin is False

            class _Same(_Fake):
                latest = "1"

            assert await _fetch_source("demo", _Same, None, session) is None
            with pytest.raises(TypeError, match="not an Updater"):
                await _fetch_source("demo", _NotUpdater, None, session)

    asyncio.run(run())


def test_blank_and_long_probe_errors_stay_bounded() -> None:
    async def fetch_source(
        name: str,
        updater_cls: type[object],
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
    ) -> UpdateOffer | None:
        _ = (name, updater_cls, current, session)
        raise RuntimeError("x" * 500 if name == "long" else "")

    async def fetch_ref(
        ref: FlakeInputRef, session: aiohttp.ClientSession
    ) -> RefUpdateResult:
        _ = (ref, session)
        raise AssertionError

    async def run() -> tuple[ProbeFailure, ...]:
        async with aiohttp.ClientSession() as session:
            _offers, failures = await discover_offers(
                updaters={"long": object, "blank": object},
                currents={},
                refs=(),
                session=session,
                security=frozenset(),
                main_fix=frozenset(),
                fetch_source=fetch_source,
                fetch_ref=fetch_ref,
            )
            return failures

    failures = {item.name: item.error for item in asyncio.run(run())}
    assert len(failures["long"]) == 200
    assert failures["blank"] == "RuntimeError"


def test_fetch_ref_uses_the_flake_ref_check(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(
        ref: FlakeInputRef, session: aiohttp.ClientSession
    ) -> RefUpdateResult:
        _ = session
        return RefUpdateResult(name=ref.name, current_ref="old", latest_ref="new")

    monkeypatch.setattr("lib.update.pull_request_discover.check_flake_ref_update", fake)

    async def run() -> RefUpdateResult:
        async with aiohttp.ClientSession() as session:
            return await _fetch_ref(
                FlakeInputRef("nixpkgs", "NixOS", "nixpkgs", "old", "github"),
                session,
            )

    result = asyncio.run(run())
    assert result.latest_ref == "new"


def test_discover_live_plans_an_empty_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib.update.pull_request_discover.ensure_updaters_loaded", dict)
    monkeypatch.setattr(
        "lib.update.pull_request_discover.load_all_sources",
        lambda: type("Sources", (), {"entries": {}})(),
    )
    monkeypatch.setattr(
        "lib.update.pull_request_discover.get_flake_inputs_with_refs", tuple
    )

    async def run() -> object:
        return await discover_live()

    result = asyncio.run(run())
    assert result.updates == ()
    assert result.failures == ()


def test_source_without_a_version_string_is_blank() -> None:
    class _NoVersion(_Fake):
        async def fetch_latest(
            self, session: aiohttp.ClientSession, *, context: object
        ) -> VersionInfo:
            _ = (session, context)
            return VersionInfo(version="9")

    current = SourceEntry.model_validate({
        "version": None,
        "hashes": {"sha256": "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="},
    })

    async def run() -> UpdateOffer | None:
        async with aiohttp.ClientSession() as session:
            return await _fetch_source("demo", _NoVersion, current, session)

    offer = asyncio.run(run())
    assert offer is not None
    assert offer.current == ""
