"""Reason release discovery and realized bundle identity contracts."""

import plistlib
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lib.tests._updater_helpers import run_async
from lib.update.candidate import ResolvedVersion
from lib.update.updaters import UpdateContext
from packages.ara import updater, validate_artifact


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


@pytest.mark.parametrize(
    "headers",
    [
        {"x-ara-desktop-version": "0.1.66"},
        {"X-Ara-Desktop-Version": " 0.1.66 "},
    ],
)
def test_reason_uses_download_header_when_feed_lags(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    """Nix hashes the redirect; its x-ara-desktop-version is the pin."""
    _feed(
        monkeypatch,
        {"name": "0.1.64", "url": "https://example.test/private?signature=test"},
    )
    info = run_async(
        updater.AraUpdater().fetch_latest(
            _FakeSession(
                _FakeResponse(
                    headers={
                        **headers,
                        "location": (
                            "https://ara-desktop-releases.t3.storage.dev"
                            "/desktop/stable/0.1.66/Reason.dmg?signature=test"
                        ),
                    }
                )
            ),
            context=UpdateContext(current=None),
        )
    )
    assert info.version == "0.1.66"


def test_reason_rejects_location_that_is_not_the_versioned_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public redirect is latest-only; refuse a Location for another release."""
    _feed(monkeypatch, {"name": "0.1.71"})
    with pytest.raises(RuntimeError, match="versioned object for 0.1.73"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(
                    _FakeResponse(
                        headers={
                            "x-ara-desktop-version": "0.1.73",
                            "location": (
                                "https://ara-desktop-releases.t3.storage.dev"
                                "/desktop/stable/0.1.71/Reason.dmg?signature=test"
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
    with pytest.raises(RuntimeError, match="versioned object for 0.1.73"):
        run_async(
            updater.AraUpdater().fetch_latest(
                _FakeSession(
                    _FakeResponse(headers={"x-ara-desktop-version": "0.1.73"})
                ),
                context=UpdateContext(current=None),
            )
        )


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
