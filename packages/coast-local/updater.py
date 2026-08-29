"""Fail-closed updater for the public Coast Local DMG."""

import base64
import binascii
import email.utils
import hashlib
import re
from datetime import UTC
from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar

import aiohttp

from lib.nix.models.sources import HashCollection
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib.nix.models.sources import SourceEntry, SourceHashes
    from lib.update.updaters import UpdateContext

_DOWNLOAD_URL = "https://dmg.cdn-coast.app/Coast%20Local.dmg"
_ALLOWED_CONTENT_TYPES = frozenset({
    "application/octet-stream",
    "application/x-apple-diskimage",
})
_DMG_SIZE_RANGE = range(10 * 1024 * 1024, 2 * 1024 * 1024 * 1024)
_SHA256_DIGEST_SIZE = 32
_STRONG_ETAG = re.compile(r'^"(?P<opaque>[!#-~]{1,256})"$')
_HEX_ETAG = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}")
_DISCOVERY_VERSION = re.compile(
    r"^(?P<date>[0-9]{8})\.(?P<digest>[0-9a-f]{32}|[0-9a-f]{64})$"
)


def _require_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None or not value.strip():
        msg = f"Coast Local DMG response is missing {name}"
        raise RuntimeError(msg)
    return value.strip()


def _parse_artifact_identity(headers: Mapping[str, str]) -> str:
    """Return a strict discovery token for one public DMG representation."""
    content_type = (
        _require_header(headers, "Content-Type").partition(";")[0].strip().lower()
    )
    if content_type not in _ALLOWED_CONTENT_TYPES:
        msg = f"Unexpected Coast Local DMG Content-Type: {content_type!r}"
        raise RuntimeError(msg)

    content_encoding = headers.get("Content-Encoding", "identity").strip().lower()
    if content_encoding != "identity":
        msg = f"Unexpected Coast Local DMG Content-Encoding: {content_encoding!r}"
        raise RuntimeError(msg)

    content_length_text = _require_header(headers, "Content-Length")
    try:
        content_length = int(content_length_text)
    except ValueError as exc:
        msg = f"Invalid Coast Local DMG Content-Length: {content_length_text!r}"
        raise RuntimeError(msg) from exc
    if content_length not in _DMG_SIZE_RANGE:
        msg = f"Implausible Coast Local DMG Content-Length: {content_length}"
        raise RuntimeError(msg)

    etag_text = _require_header(headers, "ETag")
    etag_match = _STRONG_ETAG.fullmatch(etag_text)
    if etag_match is None:
        msg = f"Coast Local DMG requires a bounded strong ETag: {etag_text!r}"
        raise RuntimeError(msg)

    last_modified_text = _require_header(headers, "Last-Modified")
    try:
        last_modified = email.utils.parsedate_to_datetime(last_modified_text)
    except (TypeError, ValueError) as exc:
        msg = f"Invalid Coast Local DMG Last-Modified: {last_modified_text!r}"
        raise RuntimeError(msg) from exc
    if last_modified.tzinfo is None:
        msg = f"Coast Local DMG Last-Modified lacks a timezone: {last_modified_text!r}"
        raise RuntimeError(msg)

    date_token = last_modified.astimezone(UTC).strftime("%Y%m%d")
    opaque_etag = etag_match.group("opaque")
    discovery_digest = (
        opaque_etag.lower()
        if _HEX_ETAG.fullmatch(opaque_etag)
        else hashlib.sha256(etag_text.encode("ascii")).hexdigest()
    )
    return f"{date_token}.{discovery_digest}"


def _content_version(hashes: SourceHashes) -> str:
    """Version the mutable download by its complete Nix SHA-256 digest."""
    hash_value = HashCollection.from_value(hashes).primary_hash()
    if hash_value is None or not hash_value.startswith("sha256-"):
        msg = "Coast Local requires one platform-independent SHA-256 hash"
        raise RuntimeError(msg)
    try:
        digest = base64.b64decode(hash_value.removeprefix("sha256-"), validate=True)
    except (binascii.Error, ValueError) as exc:
        msg = f"Invalid Coast Local SRI hash: {hash_value!r}"
        raise RuntimeError(msg) from exc
    if len(digest) != _SHA256_DIGEST_SIZE:
        msg = f"Invalid Coast Local SHA-256 digest length: {len(digest)}"
        raise RuntimeError(msg)
    return f"sha256-{digest.hex()}"


@register_updater
class CoastLocalUpdater(DownloadHashUpdater):
    """Track the public arm64 DMG without querying its device-gated appcast."""

    name = "coast-local"
    supported_platforms = ("aarch64-darwin",)
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": _DOWNLOAD_URL}

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve a content identity using only generic public CDN headers."""
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        headers = {
            "Accept": "application/x-apple-diskimage, application/octet-stream",
            "Accept-Encoding": "identity",
            "User-Agent": self.config.default_user_agent,
        }
        async with session.request(
            "HEAD",
            _DOWNLOAD_URL,
            allow_redirects=False,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status != HTTPStatus.OK:
                msg = (
                    f"Coast Local DMG discovery failed with HTTP {response.status} "
                    f"{response.reason}"
                )
                raise RuntimeError(msg)
            if str(response.url) != _DOWNLOAD_URL:
                msg = f"Coast Local DMG resolved to an unexpected URL: {response.url}"
                raise RuntimeError(msg)
            version = _parse_artifact_identity(response.headers)
        return VersionInfo(version=version)

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Rehash every time because the public Coast URL is mutable."""
        _ = (context, info)
        return False

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the complete content digest rather than mutable CDN metadata."""
        if _DISCOVERY_VERSION.fullmatch(info.version) is None:
            msg = f"Invalid Coast Local discovery version: {info.version!r}"
            raise RuntimeError(msg)
        content_info = VersionInfo(
            version=_content_version(hashes),
            metadata=info.metadata,
        )
        return super().build_result(content_info, hashes)
