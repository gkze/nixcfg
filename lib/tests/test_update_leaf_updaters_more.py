"""Focused branch coverage for package-owned updater entrypoints."""

from __future__ import annotations

import plistlib
from collections.abc import Callable
from types import ModuleType
from typing import Protocol, cast

import pytest

from lib.tests._updater_helpers import (
    load_repo_module_for_test,
    run_async,
    updater_from_module,
)
from lib.update.updaters import VersionInfo
from lib.update.updaters import strategies as updater_strategies
from lib.update.updaters.metadata import AssetURLsMetadata, DownloadUrlMetadata
from lib.update.updaters.vendor_feeds import SparkleAppcastItem


def _load(path: str) -> ModuleType:
    return load_repo_module_for_test(path, prefix="test_leaf")


class _PackageUpdater(Protocol):
    async def fetch_latest(self, session: object) -> VersionInfo: ...

    def get_download_url(self, platform: str, info: VersionInfo) -> str: ...


class _AssetNameUpdater(Protocol):
    def _asset_name(self, version: str, platform_value: str) -> str: ...


class _PlatformUpdater(_PackageUpdater, Protocol):
    PLATFORMS: dict[str, str]


def _updater(module: ModuleType) -> _PackageUpdater:
    return cast("_PackageUpdater", updater_from_module(module))


def _download_url(info: VersionInfo) -> str:
    metadata = info.metadata
    assert isinstance(metadata, DownloadUrlMetadata)
    return metadata.url


async def _fake_sparkle_items(
    *_args: object,
    item: SparkleAppcastItem,
    **_kwargs: object,
) -> tuple[SparkleAppcastItem, ...]:
    return (item,)


def _patch_dependency(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    name: str,
    value: object,
) -> None:
    target = module if hasattr(module, name) else updater_strategies
    monkeypatch.setattr(target, name, value)


@pytest.mark.parametrize(
    ("path", "item", "expected_version", "expected_url"),
    [
        (
            "packages/airfoil/updater.py",
            SparkleAppcastItem("5.12.6", None, None),
            "5.12.6",
            None,
        ),
        (
            "packages/arc/updater.py",
            SparkleAppcastItem("122.0", "122.0.1", "https://example.com/Arc.zip"),
            "122.0.1",
            "https://example.com/Arc.zip",
        ),
        (
            "packages/ghostty-tip/updater.py",
            SparkleAppcastItem(
                "1234",
                None,
                "https://tip.files.ghostty.org/"
                "0123456789abcdef0123456789abcdef01234567/Ghostty.dmg",
            ),
            "1234-0123456789abcdef0123456789abcdef01234567",
            "https://tip.files.ghostty.org/"
            "0123456789abcdef0123456789abcdef01234567/Ghostty.dmg",
        ),
        (
            "packages/macai/updater.py",
            SparkleAppcastItem("10", "1.2.3", "https://example.com/macai.zip"),
            "1.2.3",
            "https://example.com/macai.zip",
        ),
        (
            "packages/nordvpn/updater.py",
            SparkleAppcastItem("9.1.0", None, None),
            "9.1.0",
            None,
        ),
        (
            "packages/tailscale-app/updater.py",
            SparkleAppcastItem("1.82.5", None, None),
            "1.82.5",
            None,
        ),
    ],
)
def test_sparkle_package_updaters(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    item: SparkleAppcastItem,
    expected_version: str,
    expected_url: str | None,
) -> None:
    module = _load(path)

    async def fake_fetch(
        *args: object, **kwargs: object
    ) -> tuple[SparkleAppcastItem, ...]:
        return await _fake_sparkle_items(*args, item=item, **kwargs)

    _patch_dependency(monkeypatch, module, "fetch_sparkle_appcast_items", fake_fetch)
    info = run_async(_updater(module).fetch_latest(object()))
    assert info.version == expected_version
    if expected_url is not None:
        assert _download_url(info) == expected_url


def test_ghostty_tip_requires_commit_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("packages/ghostty-tip/updater.py")

    async def fake_fetch(
        *args: object, **kwargs: object
    ) -> tuple[SparkleAppcastItem, ...]:
        _ = (args, kwargs)
        return (
            SparkleAppcastItem(
                "1234",
                None,
                "https://tip.files.ghostty.org/Ghostty.dmg",
            ),
        )

    _patch_dependency(monkeypatch, module, "fetch_sparkle_appcast_items", fake_fetch)
    with pytest.raises(RuntimeError, match="Could not parse Ghostty tip commit"):
        run_async(_updater(module).fetch_latest(object()))


@pytest.mark.parametrize(
    ("path", "payload", "expected_version", "expected_url"),
    [
        (
            "packages/antigravity/updater.py",
            {"url": "https://host/antigravity-hub/1.2.3/app.zip"},
            "1.2.3",
            "https://host/antigravity-hub/1.2.3/app.dmg",
        ),
        (
            "packages/claude/updater.py",
            {
                "releases": [
                    {
                        "updateTo": {
                            "version": "0.14.2",
                            "url": "https://example.com/Claude.zip",
                        }
                    }
                ]
            },
            "0.14.2",
            "https://example.com/Claude.zip",
        ),
        (
            "packages/docker-desktop/updater.py",
            {
                "Items": [
                    {
                        "AppVersion": "4.42.0",
                        "BuildNumber": "190950",
                        "Artifacts": [
                            {"Type": "zip", "URL": "https://example.com/Docker.zip"},
                            {"Type": "dmg", "URL": "https://example.com/Docker.dmg"},
                        ],
                    }
                ]
            },
            "4.42.0-190950",
            "https://example.com/Docker.dmg",
        ),
        (
            "packages/onepassword/updater.py",
            {"version": "8.11.2", "sources": [{"url": "https://example.com/1p.zip"}]},
            "8.11.2",
            "https://example.com/1p.zip",
        ),
    ],
)
def test_json_download_url_package_updaters(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    expected_version: str,
    expected_url: str,
) -> None:
    module = _load(path)

    async def fake_fetch_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return payload

    _patch_dependency(monkeypatch, module, "fetch_json", fake_fetch_json)
    info = run_async(_updater(module).fetch_latest(object()))
    assert info.version == expected_version
    assert _download_url(info) == expected_url


def test_onepassword_rechecks_same_version_download_hashes() -> None:
    """1Password can replace same-version ZIPs, so updates must rehash."""
    module = _load("packages/onepassword/updater.py")

    assert _updater(module).materialize_when_current is True


@pytest.mark.parametrize(
    ("path", "payload", "expected_version"),
    [
        (
            "packages/comet/updater.py",
            {"body": {"browser_version": " 1.2.3 "}},
            "1.2.3",
        ),
        (
            "packages/warp-preview/updater.py",
            {"preview": {"version": "v0.2025.01.01"}},
            "0.2025.01.01",
        ),
    ],
)
def test_json_hash_package_updaters(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    expected_version: str,
) -> None:
    module = _load(path)

    async def fake_fetch_json(*_args: object, **_kwargs: object) -> dict[str, object]:
        return payload

    _patch_dependency(monkeypatch, module, "fetch_json", fake_fetch_json)
    assert (
        run_async(_updater(module).fetch_latest(object())).version == expected_version
    )


def test_figma_uses_per_platform_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load("packages/figma/updater.py")
    payloads = {
        "mac-arm": {"version": "125.0", "url": "https://example.com/Figma-arm.zip"},
        "mac": {"version": "125.0", "url": "https://example.com/Figma-x64.zip"},
    }

    async def fake_fetch_json(
        _session: object, url: str, **_kwargs: object
    ) -> dict[str, str]:
        channel = "mac-arm" if "/mac-arm/" in url else "mac"
        return payloads[channel]

    monkeypatch.setattr(module, "fetch_json", fake_fetch_json)
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert info.version == "125.0"
    assert (
        updater.get_download_url("aarch64-darwin", info)
        == "https://example.com/Figma-arm.zip"
    )
    fallback = VersionInfo("125.0")
    assert updater.get_download_url("x86_64-darwin", fallback).endswith(
        "/Figma-125.0.zip"
    )


def test_figma_rejects_mismatched_platform_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load("packages/figma/updater.py")
    versions = iter(("125.0", "126.0"))

    async def fake_fetch_json(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"version": next(versions), "url": "https://example.com/Figma.zip"}

    monkeypatch.setattr(module, "fetch_json", fake_fetch_json)
    with pytest.raises(RuntimeError, match="mismatched versions"):
        run_async(_updater(module).fetch_latest(object()))


def test_figma_falls_back_when_metadata_lacks_platform_url() -> None:
    updater = _updater(_load("packages/figma/updater.py"))
    info = VersionInfo("125.0", metadata=AssetURLsMetadata({}))

    assert updater.get_download_url("aarch64-darwin", info).endswith("/Figma-125.0.zip")


@pytest.mark.parametrize(
    ("path", "payload", "expected_version"),
    [
        ("packages/claude-code/updater.py", b"1.0.77\n", "1.0.77"),
        ("packages/framer/updater.py", b"2025.34.1\n", "2025.34.1"),
    ],
)
def test_text_version_package_updaters(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: bytes,
    expected_version: str,
) -> None:
    module = _load(path)

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return payload

    _patch_dependency(monkeypatch, module, "fetch_url", fake_fetch_url)
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert info.version == expected_version
    platform_updater = cast("_PlatformUpdater", updater)
    assert updater.get_download_url(next(iter(platform_updater.PLATFORMS)), info)


@pytest.mark.parametrize(
    "path",
    ["packages/claude-code/updater.py", "packages/framer/updater.py"],
)
def test_text_version_package_updaters_require_version(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    module = _load(path)

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return b"  \n"

    _patch_dependency(monkeypatch, module, "fetch_url", fake_fetch_url)
    with pytest.raises(RuntimeError, match="Missing"):
        run_async(_updater(module).fetch_latest(object()))


def test_cleanshot_parses_changelog_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("packages/cleanshot/updater.py")

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return b'<span class="number">4.8.2</span>'

    monkeypatch.setattr(module, "fetch_url", fake_fetch_url)
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert info.version == "4.8.2"
    assert updater.get_download_url("aarch64-darwin", info).endswith(
        "CleanShot-X-4.8.2.dmg"
    )


def test_macfuse_parses_release_plist(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("packages/macfuse/updater.py")
    payload = plistlib.dumps({
        "Rules": [{"Version": "4.9.3", "Codebase": "https://example.com/fuse.dmg"}]
    })

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return payload

    monkeypatch.setattr(module, "fetch_url", fake_fetch_url)
    info = run_async(_updater(module).fetch_latest(object()))
    assert info.version == "4.9.3"
    assert _download_url(info) == "https://example.com/fuse.dmg"


@pytest.mark.parametrize(
    "path",
    [
        "packages/google-drive/updater.py",
        "packages/logi-options-plus/updater.py",
        "packages/spotify/updater.py",
    ],
)
def test_head_artifact_package_updaters(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    module = _load(path)

    async def fake_head_version(*_args: object, **_kwargs: object) -> str:
        return "20260101.stable"

    _patch_dependency(
        monkeypatch,
        module,
        "fetch_head_artifact_version",
        fake_head_version,
    )
    assert (
        run_async(_updater(module).fetch_latest(object())).version == "20260101.stable"
    )


@pytest.mark.parametrize(
    ("path", "platform", "version", "expected"),
    [
        (
            "packages/nordvpn/updater.py",
            "aarch64-darwin",
            "9.1.0",
            "NordVPN.pkg",
        ),
        (
            "packages/tailscale-app/updater.py",
            "aarch64-darwin",
            "1.82.5",
            "Tailscale-1.82.5-macos.pkg",
        ),
        (
            "packages/warp-preview/updater.py",
            "aarch64-darwin",
            "0.2025.01.01",
            "WarpPreview.dmg",
        ),
    ],
)
def test_hash_package_updater_download_url_fallbacks(
    path: str,
    platform: str,
    version: str,
    expected: str,
) -> None:
    updater = _updater(_load(path))
    assert updater.get_download_url(platform, VersionInfo(version)).endswith(expected)


def test_linear_uses_selected_electron_builder_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load("packages/linear/updater.py")

    async def fake_artifact_url(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return "1.2.3", "https://example.com/Linear.dmg"

    monkeypatch.setattr(
        module, "fetch_electron_builder_artifact_url", fake_artifact_url
    )
    info = run_async(_updater(module).fetch_latest(object()))
    assert info.version == "1.2.3"
    assert _download_url(info) == "https://example.com/Linear.dmg"


@pytest.mark.parametrize(
    ("path", "expected_fallback"),
    [
        ("packages/loom/updater.py", "Loom-1.2.3-arm64.dmg"),
        ("packages/wave/updater.py", "Wave-darwin-arm64-1.2.3.dmg"),
    ],
)
def test_electron_builder_asset_url_package_updaters(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    expected_fallback: str,
) -> None:
    module = _load(path)

    async def fake_asset_urls(
        *_args: object, **_kwargs: object
    ) -> tuple[str, dict[str, str]]:
        return "1.2.3", {"aarch64-darwin": "https://example.com/app.dmg"}

    _patch_dependency(
        monkeypatch,
        module,
        "fetch_electron_builder_asset_urls",
        fake_asset_urls,
    )
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert isinstance(info.metadata, AssetURLsMetadata)
    assert (
        updater.get_download_url("aarch64-darwin", info)
        == "https://example.com/app.dmg"
    )
    assert updater.get_download_url("aarch64-darwin", VersionInfo("1.2.3")).endswith(
        expected_fallback
    )
    assert updater.get_download_url(
        "aarch64-darwin",
        VersionInfo("1.2.3", metadata=AssetURLsMetadata({})),
    ).endswith(expected_fallback)


def test_mole_app_pinned_version_and_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("packages/mole-app/updater.py")
    _patch_dependency(
        monkeypatch,
        module,
        "read_pinned_source_version",
        lambda _name: "1.2.3",
    )
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert info.version == "1.2.3"
    assert updater.get_download_url("aarch64-darwin", info).endswith(".tar.gz")


@pytest.mark.parametrize(
    ("path", "version", "platform", "expected"),
    [
        ("packages/codeedit/updater.py", "1.0.0", "darwin", "CodeEdit.dmg"),
        (
            "packages/freelens/updater.py",
            "1.0.0",
            "arm64",
            "Freelens-1.0.0-macos-arm64.dmg",
        ),
        (
            "packages/keepingyouawake/updater.py",
            "1.0.0",
            "darwin",
            "KeepingYouAwake-1.0.0.zip",
        ),
        (
            "packages/pants-preview/updater.py",
            "1.0.0",
            "linux-x86_64",
            "scie-pants-linux-x86_64",
        ),
        ("packages/rio/updater.py", "1.0.0", "darwin", "rio.dmg"),
        ("packages/yaak-beta/updater.py", "1.0.0", "aarch64", "Yaak_1.0.0_aarch64.dmg"),
    ],
)
def test_github_asset_name_updaters(
    path: str,
    version: str,
    platform: str,
    expected: str,
) -> None:
    updater = cast("_AssetNameUpdater", _updater(_load(path)))
    asset_name = cast("Callable[[str, str], str]", updater._asset_name)
    assert asset_name(version, platform) == expected


def test_signal_beta_release_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("packages/signal-beta/updater.py")
    releases: list[object] = [
        {"draft": True, "prerelease": True, "tag_name": "v7.0.0-beta.1"},
        {"draft": False, "prerelease": False, "tag_name": "v7.0.0"},
        {"draft": False, "prerelease": True, "tag_name": "v7.1.0-beta.2"},
    ]

    async def fake_releases(*_args: object, **_kwargs: object) -> list[object]:
        return releases

    monkeypatch.setattr(module, "fetch_github_api_paginated", fake_releases)
    updater = _updater(module)
    info = run_async(updater.fetch_latest(object()))
    assert info.version == "7.1.0-beta.2"
    assert updater.get_download_url("aarch64-darwin", info).endswith(
        "arm64-7.1.0-beta.2.zip"
    )


@pytest.mark.parametrize(
    ("releases", "match"),
    [
        ([object()], "Unexpected Signal release payload"),
        ([{"draft": False, "prerelease": True}], "Missing Signal release tag"),
        ([{"draft": False, "prerelease": False, "tag_name": "v7.1.0"}], "No Signal"),
    ],
)
def test_signal_beta_rejects_bad_release_payloads(
    monkeypatch: pytest.MonkeyPatch,
    releases: list[object],
    match: str,
) -> None:
    module = _load("packages/signal-beta/updater.py")

    async def fake_releases(*_args: object, **_kwargs: object) -> list[object]:
        return releases

    monkeypatch.setattr(module, "fetch_github_api_paginated", fake_releases)
    with pytest.raises((RuntimeError, TypeError), match=match):
        run_async(_updater(module).fetch_latest(object()))


@pytest.mark.parametrize("tag", ["7.1.0-beta.2", "v7.1.0"])
def test_signal_beta_rejects_unexpected_tags(tag: str) -> None:
    updater = _updater(_load("packages/signal-beta/updater.py"))
    with pytest.raises(RuntimeError):
        updater._normalize_release_version(tag)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("packages/antigravity/updater.py", {"url": "https://example.com/app.zip"}),
        ("packages/arc/updater.py", None),
        ("packages/claude/updater.py", {"releases": []}),
        ("packages/cleanshot/updater.py", b"no version here"),
        ("packages/comet/updater.py", {"body": {"browser_version": "   "}}),
        ("packages/docker-desktop/updater.py", {"Items": []}),
        (
            "packages/docker-desktop/updater.py",
            {
                "Items": [
                    {"AppVersion": "4.42.0", "BuildNumber": "190950", "Artifacts": []}
                ]
            },
        ),
        ("packages/macfuse/updater.py", plistlib.dumps({"Rules": []})),
        ("packages/onepassword/updater.py", {"version": "8.0.0", "sources": []}),
        ("packages/warp-preview/updater.py", {"preview": {"version": "v"}}),
    ],
)
def test_leaf_updaters_reject_invalid_vendor_payloads(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: object,
) -> None:
    module = _load(path)
    if payload is None:

        async def fake_sparkle(
            *_args: object, **_kwargs: object
        ) -> tuple[SparkleAppcastItem, ...]:
            return (SparkleAppcastItem("not-a-semver", None, ""),)

        monkeypatch.setattr(
            updater_strategies, "fetch_sparkle_appcast_items", fake_sparkle
        )
    elif isinstance(payload, bytes):

        async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
            return payload

        monkeypatch.setattr(module, "fetch_url", fake_fetch_url)
    else:

        async def fake_fetch_json(*_args: object, **_kwargs: object) -> object:
            return payload

        _patch_dependency(monkeypatch, module, "fetch_json", fake_fetch_json)

    with pytest.raises((RuntimeError, TypeError)):
        run_async(_updater(module).fetch_latest(object()))
