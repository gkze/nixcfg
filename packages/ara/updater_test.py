"""Reason release discovery and realized bundle identity contracts."""

from __future__ import annotations

import plistlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from lib.tests._updater_helpers import run_async
from lib.update.candidate import ResolvedVersion
from lib.update.events import EventSink, ignore_event
from lib.update.updaters import UpdateContext
from packages.ara import updater, validate_artifact

if TYPE_CHECKING:
    from lib.update.config import UpdateConfig


@dataclass(slots=True)
class _FakeResponse:
    status: int = 307
    reason: str = "Temporary Redirect"
    headers: dict[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def head(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


def _feed(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    async def feed(_session, url, *, config):
        assert url == updater.AraUpdater.FEED_URL
        assert config is not None
        return payload

    monkeypatch.setattr(updater, "fetch_json", feed)


def test_reason_feed_does_not_persist_signed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The portable candidate retains only the version and public redirect."""
    _feed(
        monkeypatch,
        {"name": "0.1.57", "url": "https://example.test/private?signature=test"},
    )
    instance = updater.AraUpdater()
    session = _FakeSession(
        _FakeResponse(
            headers={
                "location": (
                    "https://ara-desktop-releases.t3.storage.dev"
                    "/desktop/stable/0.1.57/Reason.dmg?signature=test"
                )
            }
        )
    )
    info = run_async(
        instance.fetch_latest(session, context=UpdateContext(current=None))
    )
    saved = ResolvedVersion.capture(info)
    assert saved.version == "0.1.57"
    assert saved.metadata is None
    assert instance.get_download_url("aarch64-darwin", saved.restore()) == (
        "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    )
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == instance.PLATFORMS["aarch64-darwin"]
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"].total == instance.config.default_timeout


@pytest.mark.parametrize("payload", [{}, {"name": 57}, []])
def test_reason_feed_rejects_missing_version(
    monkeypatch: pytest.MonkeyPatch, payload
) -> None:
    """Malformed vendor data must fail before hashing a mutable download."""
    _feed(monkeypatch, payload)
    with pytest.raises((TypeError, ValueError)):
        run_async(
            updater.AraUpdater().fetch_latest(None, context=UpdateContext(current=None))
        )


def test_reason_uses_location_version_when_feed_and_header_lag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nix GETs the signed Location; /desktop/stable/<ver>/ is the pin."""
    _feed(
        monkeypatch,
        {"name": "0.1.64", "url": "https://example.test/private?signature=test"},
    )
    info = run_async(
        updater.AraUpdater().fetch_latest(
            _FakeSession(
                _FakeResponse(
                    headers={
                        "x-ara-desktop-version": "0.1.64",
                        "location": (
                            "https://ara-desktop-releases.t3.storage.dev"
                            "/desktop/stable/0.1.84/Reason.dmg?signature=test"
                        ),
                    }
                )
            ),
            context=UpdateContext(current=None),
        )
    )
    assert info.version == "0.1.84"


@pytest.mark.parametrize(
    "location",
    [
        "https://ara-desktop-releases.t3.storage.dev/desktop/stable/0.1.84",
        "https://ara-desktop-releases.t3.storage.dev/desktop/stable/",
        "https://ara-desktop-releases.t3.storage.dev/desktop/stable//Reason.dmg",
    ],
)
def test_reason_rejects_location_without_versioned_object_segment(
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    """A stable prefix without /<ver>/ is not an immutable pin."""
    _feed(monkeypatch, {"name": "0.1.84"})
    with pytest.raises(RuntimeError, match="not a versioned object"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(_FakeResponse(headers={"location": location})),
                context=UpdateContext(current=None),
            )
        )


def test_reason_rejects_location_that_is_not_the_versioned_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public redirect is latest-only; refuse a Location without a pin."""
    _feed(monkeypatch, {"name": "0.1.71"})
    with pytest.raises(RuntimeError, match="not a versioned object"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(
                    _FakeResponse(
                        headers={
                            "x-ara-desktop-version": "0.1.73",
                            "location": (
                                "https://ara-desktop-releases.t3.storage.dev"
                                "/desktop/latest/Reason.dmg?signature=test"
                            ),
                        }
                    )
                ),
                context=UpdateContext(current=None),
            )
        )


def test_reason_rejects_missing_versioned_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version header without the Tigris object path is not an immutable pin."""
    _feed(monkeypatch, {"name": "0.1.73"})
    with pytest.raises(RuntimeError, match="not a versioned object"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(
                    _FakeResponse(headers={"x-ara-desktop-version": "0.1.73"})
                ),
                context=UpdateContext(current=None),
            )
        )


def test_reason_fod_name_matches_fetch_dmg() -> None:
    """Prefetch and fetch-dmg must share one store name."""
    assert updater.AraUpdater().fod_name("0.1.84") == "Reason_0.1.84_aarch64.dmg"


def test_reason_prefetch_uses_fod_name_not_url_basename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """38215834334 hashed desktop-download; cascade then rebuilt after 0.1.85."""
    seen: list[tuple[str, str | None]] = []

    async def fake_hash(
        source: str,
        url: str,
        *,
        config: UpdateConfig,
        emit: EventSink = ignore_event,
        name: str | None = None,
    ) -> str:
        _ = (source, config, emit)
        seen.append((url, name))
        return "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    monkeypatch.setattr(updater, "compute_sri_hash", fake_hash)
    hashes = run_async(
        updater.AraUpdater().fetch_hashes(
            updater.VersionInfo(version="0.1.84"),
            _FakeSession(_FakeResponse()),
            context=UpdateContext(current=None),
        )
    )
    assert hashes == {
        "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    }
    assert seen == [
        (
            updater.AraUpdater.PLATFORMS["aarch64-darwin"],
            "Reason_0.1.84_aarch64.dmg",
        )
    ]


def test_reason_validates_aarch64_darwin_package() -> None:
    """Closures was the first Nix fetch; packages must realize the FOD too."""
    validations = updater.AraUpdater.get_derivation_validations()
    assert len(validations) == 1
    assert validations[0].mode == "build"
    assert validations[0].systems == ("aarch64-darwin",)
    assert validations[0].installable == "path:.#pkgs.{system}.{name}"


@pytest.mark.parametrize("status", [403, 500])
def test_reason_download_discovery_does_not_hide_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """A failed public endpoint must not fall back to the feed version."""
    _feed(monkeypatch, {"name": "0.1.64"})
    with pytest.raises(RuntimeError, match=f"discovery failed with HTTP {status}"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(_FakeResponse(status=status, reason="Unavailable")),
                context=UpdateContext(current=None),
            )
        )


@pytest.fixture
def bundle_info(tmp_path: Path) -> Path:
    info = tmp_path / "Info.plist"
    info.write_bytes(
        plistlib.dumps({
            "CFBundleIdentifier": "so.ara.desktop",
            "CFBundleExecutable": "Reason",
            "CFBundleShortVersionString": "0.1.57",
        })
    )
    return info


def test_reason_install_check(
    bundle_info: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the same entry point used by the Nix installation phase."""
    monkeypatch.setattr(sys, "argv", ["validate_artifact", str(bundle_info), "0.1.57"])
    validate_artifact.main()


def test_reason_install_accepts_rebranded_bundle_identifier(tmp_path: Path) -> None:
    """0.1.64+ ships com.reasonmachines.desktop after the Ara-to-Reason rename."""
    info = tmp_path / "Info.plist"
    info.write_bytes(
        plistlib.dumps({
            "CFBundleIdentifier": "com.reasonmachines.desktop",
            "CFBundleExecutable": "Reason",
            "CFBundleShortVersionString": "0.1.64",
        })
    )
    validate_artifact.validate(info, "0.1.64")


@pytest.mark.parametrize(
    "key", ["CFBundleIdentifier", "CFBundleExecutable", "CFBundleShortVersionString"]
)
def test_reason_install_rejects_mismatched_identity(
    bundle_info: Path, key: str
) -> None:
    """Catch vendor swaps and releases changing between discovery and hashing."""
    info = plistlib.loads(bundle_info.read_bytes())
    info[key] = "different"
    bundle_info.write_bytes(plistlib.dumps(info))
    with pytest.raises(ValueError, match="does not match selected release"):
        validate_artifact.validate(bundle_info, "0.1.57")
