"""Small parsers for vendor-owned update feeds."""

from __future__ import annotations

import email.utils
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

import yaml
from defusedxml import ElementTree

from lib import json_utils
from lib.update.net import fetch_headers, fetch_url

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from xml.etree.ElementTree import Element

    import aiohttp

    from lib.update.config import UpdateConfig


SPARKLE_NS = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


@dataclass(frozen=True, slots=True)
class SparkleAppcastItem:
    """One release item from a Sparkle appcast."""

    version: str | None
    short_version: str | None
    url: str | None


def _xml_text(item: Element, name: str) -> str | None:
    value = item.findtext(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_sparkle_appcast(
    payload: bytes,
    *,
    context: str,
) -> tuple[SparkleAppcastItem, ...]:
    """Parse release items from a Sparkle appcast payload."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        snippet = payload[:120].decode(errors="replace")
        msg = f"Invalid Sparkle appcast XML from {context}: {snippet}"
        raise RuntimeError(msg) from exc

    items: list[SparkleAppcastItem] = []
    for item in root.findall("./channel/item"):
        enclosure = item.find("enclosure")
        url: str | None = None
        version: str | None = None
        short_version: str | None = None
        if enclosure is not None:
            url = enclosure.attrib.get("url")
            if url is not None:
                url = urllib.parse.urljoin(context, url.strip())
            version = enclosure.attrib.get(f"{SPARKLE_NS}version")
            short_version = enclosure.attrib.get(f"{SPARKLE_NS}shortVersionString")
        version = (version.strip() if version else None) or _xml_text(
            item,
            f"{SPARKLE_NS}version",
        )
        short_version = (short_version.strip() if short_version else None) or _xml_text(
            item,
            f"{SPARKLE_NS}shortVersionString",
        )
        items.append(
            SparkleAppcastItem(
                version=version,
                short_version=short_version,
                url=url,
            )
        )

    if not items:
        msg = f"No items found in Sparkle appcast {context}"
        raise RuntimeError(msg)
    return tuple(items)


async def fetch_sparkle_appcast_items(
    session: aiohttp.ClientSession,
    url: str,
    *,
    config: UpdateConfig,
    user_agent: str = "Sparkle/2.0",
) -> tuple[SparkleAppcastItem, ...]:
    """Fetch and parse one Sparkle appcast."""
    payload = await fetch_url(
        session,
        url,
        user_agent=user_agent,
        request_timeout=config.default_timeout,
        config=config,
    )
    return parse_sparkle_appcast(payload, context=url)


def require_version(value: str | None, *, context: str) -> str:
    """Return a non-empty version string from a feed field."""
    if value is None or not value.strip():
        msg = f"Missing version in {context}"
        raise RuntimeError(msg)
    return value.strip()


def require_url(value: str | None, *, context: str) -> str:
    """Return a non-empty URL string from a feed field."""
    if value is None or not value.strip():
        msg = f"Missing download URL in {context}"
        raise RuntimeError(msg)
    return value.strip()


async def fetch_electron_builder_feed(
    session: aiohttp.ClientSession,
    url: str,
    *,
    config: UpdateConfig,
) -> tuple[str, tuple[str, ...]]:
    """Fetch an electron-builder latest-mac.yml feed."""
    payload = await fetch_url(
        session,
        url,
        request_timeout=config.default_timeout,
        config=config,
    )
    loaded = yaml.safe_load(payload.decode())
    payload_map = json_utils.as_object_dict(loaded, context=url)
    version = json_utils.get_required_str(payload_map, "version", context=url).strip()
    if not version:
        msg = f"Missing version in {url}"
        raise RuntimeError(msg)

    files = json_utils.as_object_list(payload_map.get("files"), context=f"{url} files")
    urls: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        item_map = json_utils.as_object_dict(item, context=f"{url} file")
        file_url = item_map.get("url")
        if isinstance(file_url, str) and file_url:
            urls.append(urllib.parse.urljoin(url, file_url))
    if not urls:
        msg = f"No file URLs found in {url}"
        raise RuntimeError(msg)
    return version, tuple(urls)


def select_feed_url(
    urls: tuple[str, ...],
    predicate: Callable[[str], bool],
    *,
    context: str,
) -> str:
    """Select one URL from a parsed feed."""
    for url in urls:
        if predicate(url):
            return url
    msg = f"No matching artifact URL found in {context}"
    raise RuntimeError(msg)


async def fetch_electron_builder_artifact_url(
    session: aiohttp.ClientSession,
    url: str,
    predicate: Callable[[str], bool],
    *,
    config: UpdateConfig,
) -> tuple[str, str]:
    """Fetch an electron-builder feed and select one artifact URL."""
    version, urls = await fetch_electron_builder_feed(session, url, config=config)
    return version, select_feed_url(urls, predicate, context=url)


async def fetch_electron_builder_asset_urls(
    session: aiohttp.ClientSession,
    url: str,
    selectors: Mapping[str, Callable[[str, str], bool]],
    *,
    config: UpdateConfig,
) -> tuple[str, dict[str, str]]:
    """Fetch an electron-builder feed and select per-platform artifact URLs."""
    version, urls = await fetch_electron_builder_feed(session, url, config=config)
    return version, {
        platform: select_feed_url(
            urls,
            lambda artifact_url, selector=selector: selector(version, artifact_url),
            context=url,
        )
        for platform, selector in selectors.items()
    }


async def fetch_head_artifact_version(
    session: aiohttp.ClientSession,
    url: str,
    *,
    config: UpdateConfig,
) -> str:
    """Build a stable version token for mutable vendor download URLs."""
    headers = await fetch_headers(
        session,
        url,
        request_timeout=config.default_timeout,
        config=config,
    )
    etag = headers.get("ETag", "").strip().strip('"')
    etag = re.sub(r"[^A-Za-z0-9._-]+", "-", etag).strip("-")

    last_modified = headers.get("Last-Modified")
    if last_modified:
        parsed = email.utils.parsedate_to_datetime(last_modified)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        date_token = parsed.astimezone(UTC).strftime("%Y%m%d")
        return f"{date_token}.{etag}" if etag else date_token
    if etag:
        return etag

    msg = f"Missing Last-Modified or ETag headers for {url}"
    raise RuntimeError(msg)


__all__ = [
    "SparkleAppcastItem",
    "fetch_electron_builder_artifact_url",
    "fetch_electron_builder_asset_urls",
    "fetch_electron_builder_feed",
    "fetch_head_artifact_version",
    "fetch_sparkle_appcast_items",
    "parse_sparkle_appcast",
    "require_url",
    "require_version",
    "select_feed_url",
]
