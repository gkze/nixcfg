"""Reason release discovery and realized bundle identity contracts."""

import plistlib
import sys
from pathlib import Path

import pytest

from lib.tests._updater_helpers import run_async
from lib.update.candidate import ResolvedVersion
from lib.update.updaters import UpdateContext
from packages.ara import updater, validate_artifact


def test_reason_feed_does_not_persist_signed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The portable candidate retains only the version and public redirect."""

    async def feed(_session, url, *, config):
        assert url == updater.AraUpdater.FEED_URL
        assert config is not None
        return {"name": "0.1.57", "url": "https://example.test/private?signature=test"}

    monkeypatch.setattr(updater, "fetch_json", feed)
    instance = updater.AraUpdater()
    info = run_async(instance.fetch_latest(None, context=UpdateContext(current=None)))
    saved = ResolvedVersion.capture(info)
    assert saved.version == "0.1.57"
    assert saved.metadata is None
    assert instance.get_download_url("aarch64-darwin", saved.restore()) == (
        "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    )


@pytest.mark.parametrize("payload", [{}, {"name": 57}, []])
def test_reason_feed_rejects_missing_version(
    monkeypatch: pytest.MonkeyPatch, payload
) -> None:
    """Malformed vendor data must fail before hashing a mutable download."""

    async def feed(*_args, **_kwargs):
        return payload

    monkeypatch.setattr(updater, "fetch_json", feed)
    with pytest.raises((TypeError, ValueError)):
        run_async(
            updater.AraUpdater().fetch_latest(None, context=UpdateContext(current=None))
        )


@pytest.fixture
def bundle_info(tmp_path: Path) -> Path:
    info = tmp_path / "Info.plist"
    info.write_bytes(
        plistlib.dumps({
            "CFBundleIdentifier": "so.ara.desktop",
            "CFBundleExecutable": "Ara",
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
