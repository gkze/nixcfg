"""Tests for vendor-owned updater feed helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from lib.update.updaters import vendor_feeds


def _vendor_config() -> SimpleNamespace:
    return SimpleNamespace(default_timeout=1)


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
            config=_vendor_config(),
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
            config=_vendor_config(),
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
                config=_vendor_config(),
            )
        )


def test_parse_sparkle_appcast_supports_enclosure_and_item_versions() -> None:
    """Sparkle parsing should read enclosure fields and fallback item fields."""
    payload = b"""
    <rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
      <channel>
        <item>
          <enclosure url="/App.zip" sparkle:version="100" sparkle:shortVersionString="1.0"/>
        </item>
        <item>
          <sparkle:version>200</sparkle:version>
          <sparkle:shortVersionString>2.0</sparkle:shortVersionString>
        </item>
      </channel>
    </rss>
    """

    first, second = vendor_feeds.parse_sparkle_appcast(
        payload,
        context="https://example.com/feed.xml",
    )

    assert first.version == "100"
    assert first.short_version == "1.0"
    assert first.url == "https://example.com/App.zip"
    assert second.version == "200"
    assert second.short_version == "2.0"


def test_parse_sparkle_appcast_allows_enclosure_without_url() -> None:
    """Sparkle enclosure metadata can provide versions without download URLs."""
    payload = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b"<channel><item><enclosure "
        b'sparkle:version="3" sparkle:shortVersionString="3.0" />'
        b"</item></channel></rss>"
    )

    [item] = vendor_feeds.parse_sparkle_appcast(
        payload,
        context="https://example.com/appcast.xml",
    )

    assert item.version == "3"
    assert item.short_version == "3.0"
    assert item.url is None


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"<rss>", "Invalid Sparkle"),
        (b"<rss><channel /></rss>", "No items found"),
    ],
)
def test_parse_sparkle_appcast_rejects_invalid_payloads(
    payload: bytes,
    match: str,
) -> None:
    """Bad Sparkle payloads should fail with context-rich errors."""
    with pytest.raises(RuntimeError, match=match):
        vendor_feeds.parse_sparkle_appcast(
            payload, context="https://example.com/feed.xml"
        )


def test_fetch_sparkle_appcast_items_fetches_and_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sparkle fetch helper should use the configured timeout and user agent."""
    calls: list[dict[str, object]] = []
    payload = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b"<channel><item><enclosure "
        b'url="https://example.com/App.zip" sparkle:version="100" />'
        b"</item></channel></rss>"
    )

    async def fake_fetch_url(*args: object, **kwargs: object) -> bytes:
        calls.append({"args": args, "kwargs": kwargs})
        return payload

    monkeypatch.setattr(vendor_feeds, "fetch_url", fake_fetch_url)
    config = type("Config", (), {"default_timeout": 12})()

    items = asyncio.run(
        vendor_feeds.fetch_sparkle_appcast_items(
            object(),
            "https://example.com/feed.xml",
            config=config,
            user_agent="Agent",
        )
    )

    assert items[0].version == "100"
    assert calls[0]["kwargs"] == {
        "user_agent": "Agent",
        "request_timeout": 12,
        "config": config,
    }


def test_required_vendor_feed_fields_reject_empty_values() -> None:
    """Shared vendor feed validators should report missing version and URL fields."""
    with pytest.raises(RuntimeError, match="Missing version"):
        vendor_feeds.require_version("   ", context="feed")
    with pytest.raises(RuntimeError, match="Missing download URL"):
        vendor_feeds.require_url(None, context="feed")


@pytest.mark.parametrize(
    "payload",
    [
        b"version: 1.2.3\nfiles:\n  - url: App.dmg\n",
        b"version: 1.2.3\nfiles:\n  - bad\n  - url: App.dmg\n",
    ],
)
def test_fetch_electron_builder_feed_parses_yaml(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    """Electron-builder feed parsing should return a version and joined file URLs."""

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return payload

    monkeypatch.setattr(vendor_feeds, "fetch_url", fake_fetch_url)
    version, urls = asyncio.run(
        vendor_feeds.fetch_electron_builder_feed(
            object(),
            "https://example.com/latest-mac.yml",
            config=_vendor_config(),
        )
    )

    assert version == "1.2.3"
    assert urls == ("https://example.com/App.dmg",)


@pytest.mark.parametrize(
    "payload",
    [
        b"version: ' '\nfiles:\n  - url: App.dmg\n",
        b"version: 1.2.3\nfiles: []\n",
        b"version: 1.2.3\nfiles:\n  - url: ''\n",
    ],
)
def test_fetch_electron_builder_feed_rejects_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    """Malformed electron-builder feeds should fail before returning partial data."""

    async def fake_fetch_url(*_args: object, **_kwargs: object) -> bytes:
        return payload

    monkeypatch.setattr(vendor_feeds, "fetch_url", fake_fetch_url)

    with pytest.raises(RuntimeError):
        asyncio.run(
            vendor_feeds.fetch_electron_builder_feed(
                object(),
                "https://example.com/latest-mac.yml",
                config=_vendor_config(),
            )
        )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (
            {"Last-Modified": "Tue, 02 Jan 2024 03:04:05 GMT", "ETag": '"abc/def"'},
            "20240102.abc-def",
        ),
        ({"Last-Modified": "Tue, 02 Jan 2024 03:04:05 GMT"}, "20240102"),
        ({"ETag": '"opaque"'}, "opaque"),
    ],
)
def test_fetch_head_artifact_version_uses_headers(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected: str,
) -> None:
    """Mutable download URLs should get stable version tokens from HTTP metadata."""

    async def fake_fetch_headers(*_args: object, **_kwargs: object) -> dict[str, str]:
        return headers

    monkeypatch.setattr(vendor_feeds, "fetch_headers", fake_fetch_headers)

    assert (
        asyncio.run(
            vendor_feeds.fetch_head_artifact_version(
                object(),
                "https://example.com/App.dmg",
                config=_vendor_config(),
            )
        )
        == expected
    )


def test_fetch_head_artifact_version_handles_naive_last_modified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naive parsed dates are normalized to UTC before rendering."""
    parsed = datetime.fromisoformat("2024-01-02T03:04:05")

    async def fake_fetch_headers(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"Last-Modified": "ignored"}

    monkeypatch.setattr(vendor_feeds, "fetch_headers", fake_fetch_headers)
    monkeypatch.setattr(
        vendor_feeds.email.utils, "parsedate_to_datetime", lambda _value: parsed
    )

    assert asyncio.run(
        vendor_feeds.fetch_head_artifact_version(
            object(),
            "https://example.com/App.dmg",
            config=_vendor_config(),
        )
    ) == parsed.replace(tzinfo=UTC).strftime("%Y%m%d")


def test_fetch_head_artifact_version_requires_cache_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutable URLs without stable validators cannot produce a version token."""

    async def fake_fetch_headers(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(vendor_feeds, "fetch_headers", fake_fetch_headers)

    with pytest.raises(RuntimeError, match="Missing Last-Modified or ETag"):
        asyncio.run(
            vendor_feeds.fetch_head_artifact_version(
                object(),
                "https://example.com/App.dmg",
                config=_vendor_config(),
            )
        )
