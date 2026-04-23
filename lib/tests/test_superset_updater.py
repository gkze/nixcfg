"""Dedicated tests for the Superset updater's pure-Python edge cases."""

from __future__ import annotations

import asyncio
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/superset/updater.py",
        "superset_updater_dedicated_test",
    )


def _run(awaitable):
    return asyncio.run(awaitable)


@pytest.mark.parametrize(
    ("payload", "error_type", "message"),
    [
        ([], TypeError, "Unexpected release payload type: list"),
        ({}, RuntimeError, "Missing tag_name in release payload"),
        ({"tag_name": ""}, RuntimeError, "Missing tag_name in release payload"),
        (
            {"tag_name": "desktop-v", "assets": []},
            RuntimeError,
            "Missing version segment in Superset release tag: desktop-v",
        ),
        (
            {"tag_name": "desktop-v1.2.3", "assets": "bad"},
            TypeError,
            "Missing assets in release payload for tag desktop-v1.2.3",
        ),
    ],
)
def test_fetch_latest_rejects_invalid_payload_shapes(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Reject malformed GitHub release payloads before resolving asset URLs."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )

    with pytest.raises(error_type, match=message):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_ignores_non_dict_and_empty_asset_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip malformed assets and fail if no usable download URL remains."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    "noise",
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "",
                    },
                ],
            },
        ),
    )

    with pytest.raises(
        RuntimeError, match="Could not find Superset desktop release asset"
    ):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_rejects_non_desktop_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject releases whose tag names do not follow the desktop-v convention."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "v1.2.3",
                "assets": [],
            },
        ),
    )

    with pytest.raises(RuntimeError, match="Unexpected Superset release tag format"):
        _run(updater.fetch_latest(object()))


def test_fetch_latest_returns_version_info_with_asset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the desktop version and matching AppImage asset URL."""
    module = _load_module()
    updater = module.SupersetUpdater()
    monkeypatch.setattr(
        module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "other-asset",
                        "browser_download_url": "https://example.test/other",
                    },
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "https://example.test/superset-1.2.3-x86_64.AppImage",
                    },
                ],
            },
        ),
    )

    info = _run(updater.fetch_latest(object()))

    assert info.version == "1.2.3"
    assert info.metadata == AssetURLsMetadata({
        "x86_64-linux": "https://example.test/superset-1.2.3-x86_64.AppImage"
    })


def test_asset_name_and_fallback_url_match_release_convention() -> None:
    """Build asset names and fallback URLs from the desktop tag convention."""
    module = _load_module()

    assert module.SupersetUpdater._asset_name("1.2.3", "x86_64") == (
        "superset-1.2.3-x86_64.AppImage"
    )
    assert module.SupersetUpdater._fallback_url("1.2.3", "x86_64") == (
        "https://github.com/superset-sh/superset/releases/download/"
        "desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )


def test_get_download_url_prefers_metadata_asset_urls() -> None:
    """Return metadata-provided URLs before falling back to predictable release URLs."""
    module = _load_module()
    updater = module.SupersetUpdater()
    info = VersionInfo(
        "1.2.3",
        AssetURLsMetadata({"x86_64-linux": "https://example.test/superset.AppImage"}),
    )

    assert (
        updater.get_download_url("x86_64-linux", info)
        == "https://example.test/superset.AppImage"
    )


def test_get_download_url_falls_back_when_metadata_is_missing_or_empty() -> None:
    """Fallback URL generation should handle missing or blank metadata entries."""
    module = _load_module()
    updater = module.SupersetUpdater()

    empty_metadata = VersionInfo(
        "1.2.3",
        AssetURLsMetadata({"x86_64-linux": ""}),
    )
    foreign_metadata = VersionInfo("1.2.3", {"asset_urls": {}})

    expected = (
        "https://github.com/superset-sh/superset/releases/download/"
        "desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )
    assert updater.get_download_url("x86_64-linux", empty_metadata) == expected
    assert updater.get_download_url("x86_64-linux", foreign_metadata) == expected


def test_get_download_url_rejects_unsupported_platform() -> None:
    """Unknown platforms should fail instead of inventing a download URL."""
    module = _load_module()
    updater = module.SupersetUpdater()

    with pytest.raises(RuntimeError, match="Unsupported platform for superset updater"):
        updater.get_download_url("aarch64-linux", VersionInfo("1.2.3", {}))
