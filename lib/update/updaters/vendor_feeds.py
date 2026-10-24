"""Small parsers for vendor-owned update feeds."""

import base64
import email.utils
import functools
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import yaml
from defusedxml import ElementTree

from lib import json_utils
from lib.update.net import fetch_headers, fetch_url, request

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


_SPARKLE_STRING, _SPARKLE_PERIOD, _SPARKLE_NUMBER = range(3)


def _sparkle_part_rank(part: str) -> int:
    """Rank part types as Sparkle does: number > period > string."""
    if part.isdigit():
        return _SPARKLE_NUMBER
    return _SPARKLE_PERIOD if part.startswith(".") else _SPARKLE_STRING


def _compare_sparkle_versions(left: str, right: str) -> int:
    """Compare like Sparkle's ``SUStandardVersionComparator``."""
    left_parts = re.findall(r"\d+|\.+|[^\d.]+", left)
    right_parts = re.findall(r"\d+|\.+|[^\d.]+", right)
    for a, b in zip(left_parts, right_parts, strict=False):
        rank = _sparkle_part_rank(a)
        if rank != _sparkle_part_rank(b):
            return 1 if rank > _sparkle_part_rank(b) else -1
        if rank == _SPARKLE_NUMBER and int(a) != int(b):
            return 1 if int(a) > int(b) else -1
        if rank == _SPARKLE_STRING and a != b:
            return 1 if a > b else -1
    if len(left_parts) == len(right_parts):
        return 0
    left_longer = len(left_parts) > len(right_parts)
    next_part = (left_parts if left_longer else right_parts)[
        min(len(left_parts), len(right_parts))
    ]
    # A trailing string (``1.0b1``) is a pre-release of the shorter version.
    longer_wins = _sparkle_part_rank(next_part) != _SPARKLE_STRING
    return 1 if left_longer == longer_wins else -1


def newest_sparkle_item(items: tuple[SparkleAppcastItem, ...]) -> SparkleAppcastItem:
    """Return the item a Sparkle client would install: the greatest build."""
    return max(
        items,
        key=functools.cmp_to_key(
            lambda a, b: _compare_sparkle_versions(
                a.version or a.short_version or "", b.version or b.short_version or ""
            )
        ),
    )


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


@dataclass(frozen=True, slots=True)
class ElectronBuilderFeed:
    """Release fields of an electron-builder ``latest-mac.yml`` feed."""

    version: str
    path: str | None
    urls: tuple[str, ...]


async def fetch_electron_builder_manifest(
    session: aiohttp.ClientSession,
    url: str,
    *,
    config: UpdateConfig,
) -> ElectronBuilderFeed:
    """Fetch an electron-builder feed: version, top-level path, and file URLs."""
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
    path = payload_map.get("path")
    files = json_utils.as_object_list(
        payload_map.get("files", []), context=f"{url} files"
    )
    urls = tuple(
        urllib.parse.urljoin(url, item["url"])
        for item in files
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]
    )
    return ElectronBuilderFeed(
        version=version,
        path=path if isinstance(path, str) and path else None,
        urls=urls,
    )


async def fetch_electron_builder_feed(
    session: aiohttp.ClientSession,
    url: str,
    *,
    config: UpdateConfig,
) -> tuple[str, tuple[str, ...]]:
    """Fetch an electron-builder latest-mac.yml feed's version and file URLs."""
    feed = await fetch_electron_builder_manifest(session, url, config=config)
    if not feed.urls:
        msg = f"No file URLs found in {url}"
        raise RuntimeError(msg)
    return feed.version, feed.urls


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


OMAHA_UPDATE_URL = "https://update.googleapis.com/service/update2/json"
_OMAHA_ANTI_XSSI_PREFIX = b")]}'\n"


async def fetch_omaha_payload(
    session: aiohttp.ClientSession,
    *,
    app_id: str,
    body: object,
    config: UpdateConfig,
) -> bytes:
    """POST one Google Omaha update check and return the raw response body."""
    response = await request(
        session,
        OMAHA_UPDATE_URL,
        method="POST",
        headers={
            "User-Agent": config.default_user_agent,
            "Content-Type": "application/json",
            "X-Goog-Update-Interactivity": "fg",
            "X-Goog-Update-AppId": app_id,
            "X-Goog-Update-Updater": "nixcfg-0",
        },
        json_body=body,
        config=config,
    )
    return response.body


def parse_omaha_updatecheck(
    payload: bytes,
    *,
    app_id: str,
    apps_key: str,
    label: str,
) -> dict[str, object]:
    """Return the sole ``app_id`` app's successful Omaha ``updatecheck``.

    ``apps_key`` is ``apps`` in protocol 4.0 and ``app`` in protocol 3.1.
    """
    if not payload.startswith(_OMAHA_ANTI_XSSI_PREFIX):
        msg = f"{label} Omaha response omitted its anti-XSSI prefix"
        raise RuntimeError(msg)
    try:
        payload_value = json.loads(payload.removeprefix(_OMAHA_ANTI_XSSI_PREFIX))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"{label} Omaha response was not valid JSON"
        raise RuntimeError(msg) from exc
    response = json_utils.as_object_dict(
        json_utils.as_object_dict(payload_value, context=f"{label} Omaha response").get(
            "response"
        ),
        context=f"{label} Omaha response.response",
    )
    apps = [
        json_utils.as_object_dict(app, context=f"{label} Omaha response app")
        for app in json_utils.as_object_list(
            response.get(apps_key),
            context=f"{label} Omaha response.response.{apps_key}",
        )
    ]
    matching_apps = [app for app in apps if app.get("appid") == app_id]
    if len(matching_apps) != 1:
        msg = f"{label} Omaha response contained {len(matching_apps)} matching apps"
        raise RuntimeError(msg)
    app = matching_apps[0]
    app_status = json_utils.get_required_str(
        app, "status", context=f"{label} Omaha app"
    )
    if app_status != "ok":
        msg = f"{label} Omaha app returned status {app_status!r}"
        raise RuntimeError(msg)
    updatecheck = json_utils.as_object_dict(
        app.get("updatecheck"), context=f"{label} Omaha updatecheck"
    )
    update_status = json_utils.get_required_str(
        updatecheck, "status", context=f"{label} Omaha updatecheck"
    )
    if update_status != "ok":
        msg = f"{label} Omaha updatecheck returned status {update_status!r}"
        raise RuntimeError(msg)
    return updatecheck


_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
_DEBIAN_PATH_COMPONENT = re.compile(r"[A-Za-z0-9._+~-]+")


def sha256_hex_to_sri(value: str, *, context: str) -> str:
    """Convert a vendor-published hex SHA-256 digest to an SRI hash."""
    if _SHA256_HEX.fullmatch(value) is None:
        msg = f"Invalid SHA-256 in {context}: {value!r}"
        raise RuntimeError(msg)
    return f"sha256-{base64.b64encode(bytes.fromhex(value)).decode()}"


@dataclass(frozen=True, slots=True)
class DebianPackage:
    """One validated binary package stanza from an apt ``Packages`` index."""

    version: str
    filename: str
    sri_hash: str


def _debian_fields(stanza: str, *, label: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in stanza.splitlines():
        if not line or line[0].isspace():
            continue
        name, separator, value = line.partition(":")
        if not separator:
            msg = f"Malformed {label} line: {line!r}"
            raise RuntimeError(msg)
        fields.setdefault(name, []).append(value.strip())
    return fields


def _debian_field(fields: dict[str, list[str]], name: str, *, label: str) -> str:
    values = fields.get(name, [])
    if len(values) != 1 or not values[0]:
        msg = f"{label} has invalid {name!r} field"
        raise RuntimeError(msg)
    return values[0]


def parse_debian_packages(
    payload: bytes,
    *,
    package: str,
    architecture: str,
    label: str,
) -> tuple[DebianPackage, ...]:
    """Return every validated ``package``/``architecture`` stanza in an index.

    Each match must name one version, a confined ``pool/`` path whose basename
    is ``<package>_<version>_<architecture>.deb``, and a SHA-256 digest.
    """
    try:
        text = payload.decode()
    except UnicodeDecodeError as exc:
        msg = f"{label} is not UTF-8"
        raise RuntimeError(msg) from exc
    packages: list[DebianPackage] = []
    for stanza in re.split(r"\r?\n\r?\n+", text.strip()):
        fields = _debian_fields(stanza, label=label)
        if fields.get("Package") != [package] or fields.get("Architecture") != [
            architecture
        ]:
            continue
        version = _debian_field(fields, "Version", label=label)
        filename = _debian_field(fields, "Filename", label=label)
        path = PurePosixPath(filename)
        # Debian file names omit the epoch.
        expected_name = f"{package}_{version.rpartition(':')[2]}_{architecture}.deb"
        if (
            path.is_absolute()
            or path.as_posix() != filename
            or path.parts[0] != "pool"
            or path.name != expected_name
            or any(
                component in {".", ".."}
                or _DEBIAN_PATH_COMPONENT.fullmatch(component) is None
                for component in path.parts
            )
        ):
            msg = f"{label} has invalid Filename: {filename!r}"
            raise RuntimeError(msg)
        packages.append(
            DebianPackage(
                version=version,
                filename=filename,
                sri_hash=sha256_hex_to_sri(
                    _debian_field(fields, "SHA256", label=label), context=label
                ),
            )
        )
    return tuple(packages)


def _debian_order(part: str) -> tuple[tuple[tuple[int, ...], int], ...]:
    """Key one dpkg version part: ``~`` < end < letters < other characters."""
    return tuple(
        (
            (
                *(
                    -1 if char == "~" else ord(char) + (0 if char.isalpha() else 256)
                    for char in text
                ),
                0,
            ),
            int(digits or 0),
        )
        for text, digits in re.findall(r"(\D*)(\d*)", part)
    )


def debian_version_key(version: str) -> tuple[object, ...]:
    """Order Debian ``[epoch:]upstream[-revision]`` versions like dpkg."""
    epoch, _, rest = version.rpartition(":")
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return (int(epoch or 0), _debian_order(upstream), _debian_order(revision))


__all__ = [
    "OMAHA_UPDATE_URL",
    "DebianPackage",
    "ElectronBuilderFeed",
    "SparkleAppcastItem",
    "debian_version_key",
    "fetch_electron_builder_artifact_url",
    "fetch_electron_builder_asset_urls",
    "fetch_electron_builder_feed",
    "fetch_electron_builder_manifest",
    "fetch_head_artifact_version",
    "fetch_omaha_payload",
    "fetch_sparkle_appcast_items",
    "newest_sparkle_item",
    "parse_debian_packages",
    "parse_omaha_updatecheck",
    "parse_sparkle_appcast",
    "require_url",
    "require_version",
    "select_feed_url",
    "sha256_hex_to_sri",
]
