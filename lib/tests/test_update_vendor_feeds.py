"""Tests for vendor-owned updater feed helpers."""

from __future__ import annotations

import asyncio

import pytest

from lib.update.updaters import vendor_feeds


def test_fetch_electron_builder_artifact_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select one artifact from a parsed electron-builder feed."""

    async def _feed(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> tuple[str, tuple[str, ...]]:
        _ = config
        return "1.2.3", (f"{url}/app.zip", f"{url}/app.dmg")

    monkeypatch.setattr(vendor_feeds, "fetch_electron_builder_feed", _feed)

    version, artifact_url = asyncio.run(
        vendor_feeds.fetch_electron_builder_artifact_url(
            object(),
            "https://example.com/latest-mac.yml",
            lambda url: url.endswith(".dmg"),
            config=object(),
        )
    )

    assert version == "1.2.3"
    assert artifact_url == "https://example.com/latest-mac.yml/app.dmg"


def test_fetch_electron_builder_asset_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select platform-specific artifacts from a parsed feed."""

    async def _feed(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> tuple[str, tuple[str, ...]]:
        _ = (url, config)
        return (
            "2.3.4",
            (
                "https://example.com/App-2.3.4.dmg",
                "https://example.com/App-arm64-2.3.4.dmg",
            ),
        )

    monkeypatch.setattr(vendor_feeds, "fetch_electron_builder_feed", _feed)

    version, urls = asyncio.run(
        vendor_feeds.fetch_electron_builder_asset_urls(
            object(),
            "https://example.com/latest-mac.yml",
            {
                "x86_64-darwin": lambda version, url: url.endswith(
                    f"App-{version}.dmg"
                ),
                "aarch64-darwin": lambda version, url: url.endswith(
                    f"App-arm64-{version}.dmg"
                ),
            },
            config=object(),
        )
    )

    assert version == "2.3.4"
    assert urls == {
        "x86_64-darwin": "https://example.com/App-2.3.4.dmg",
        "aarch64-darwin": "https://example.com/App-arm64-2.3.4.dmg",
    }


def test_fetch_electron_builder_asset_urls_requires_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface selector misses with the existing feed-selection error."""

    async def _feed(
        _session: object,
        url: str,
        *,
        config: object,
    ) -> tuple[str, tuple[str, ...]]:
        _ = (url, config)
        return "1.0.0", ("https://example.com/App.zip",)

    monkeypatch.setattr(vendor_feeds, "fetch_electron_builder_feed", _feed)

    with pytest.raises(RuntimeError, match="No matching artifact URL found"):
        asyncio.run(
            vendor_feeds.fetch_electron_builder_asset_urls(
                object(),
                "https://example.com/latest-mac.yml",
                {"x86_64-darwin": lambda _version, url: url.endswith(".dmg")},
                config=object(),
            )
        )
