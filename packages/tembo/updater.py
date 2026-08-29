"""Fail-closed updater for official Tembo desktop releases."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import (
    ChecksumProvidedUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import PlatformAPIMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

type JsonObject = json_utils.JsonObject

_RELEASES_ORIGIN = (
    "https://tembo-desktop-releases-844506114394.s3.us-east-1.amazonaws.com"
)
_MANIFEST_URL = f"{_RELEASES_ORIGIN}/releases/manifest.json"
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UPLOADED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DMG_CONTENT_TYPE = "application/x-apple-diskimage"
_DMG_SIZE_RANGE = range(50 * 1024 * 1024, 1024 * 1024 * 1024)


def _require_object(value: object, *, context: str) -> dict[str, object]:
    try:
        return json_utils.as_object_dict(value, context=context)
    except TypeError as exc:
        msg = f"Invalid Tembo release manifest: expected object for {context}"
        raise RuntimeError(msg) from exc


def _require_list(value: object, *, context: str) -> list[object]:
    try:
        return json_utils.as_object_list(value, context=context)
    except TypeError as exc:
        msg = f"Invalid Tembo release manifest: expected array for {context}"
        raise RuntimeError(msg) from exc


def _require_string(payload: dict[str, object], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = (
            "Invalid Tembo release manifest: expected non-empty string "
            f"{key!r} in {context}"
        )
        raise RuntimeError(msg)
    return value


def _require_exact_string(
    payload: dict[str, object],
    key: str,
    expected: str,
    *,
    context: str,
) -> str:
    value = _require_string(payload, key, context=context)
    if value != expected:
        msg = (
            f"Invalid Tembo release manifest: expected {key!r}={expected!r} "
            f"in {context}, got {value!r}"
        )
        raise RuntimeError(msg)
    return value


@register_updater
class TemboUpdater(ChecksumProvidedUpdater):
    """Track the immutable signed DMGs selected by Tembo's macOS manifest."""

    name = "tembo"
    MANIFEST_URL = _MANIFEST_URL
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }
    supported_platforms = tuple(PLATFORMS)
    _NIX_PLATFORM_BY_ARCH: ClassVar[dict[str, str]] = {
        arch: platform for platform, arch in PLATFORMS.items()
    }

    @classmethod
    def _parse_artifact(
        cls,
        raw_artifact: object,
        *,
        version: str,
        index: int,
    ) -> tuple[str, JsonObject]:
        context = f"latestByPlatform.macos.artifacts[{index}]"
        artifact = _require_object(raw_artifact, context=context)
        arch = _require_string(artifact, "arch", context=context)
        nix_platform = cls._NIX_PLATFORM_BY_ARCH.get(arch)
        if nix_platform is None:
            msg = f"Invalid Tembo release manifest: unsupported macOS arch {arch!r}"
            raise RuntimeError(msg)

        file_name = f"Tembo-{version}-{arch}.dmg"
        object_key = f"releases/{version}/{file_name}"
        latest_object_key = f"releases/latest/macos/Tembo-{arch}.dmg"
        immutable_url = f"{_RELEASES_ORIGIN}/{object_key}"
        latest_url = f"{_RELEASES_ORIGIN}/{latest_object_key}"

        _require_exact_string(artifact, "platform", "macos", context=context)
        _require_exact_string(artifact, "fileName", file_name, context=context)
        _require_exact_string(artifact, "objectKey", object_key, context=context)
        _require_exact_string(artifact, "path", immutable_url, context=context)
        _require_exact_string(
            artifact,
            "latestObjectKey",
            latest_object_key,
            context=context,
        )
        _require_exact_string(artifact, "latestPath", latest_url, context=context)
        _require_exact_string(
            artifact,
            "contentType",
            _DMG_CONTENT_TYPE,
            context=context,
        )
        _require_exact_string(
            artifact,
            "contentDisposition",
            f'attachment; filename="{file_name}"',
            context=context,
        )

        checksum = _require_string(artifact, "sha256", context=context)
        if _SHA256.fullmatch(checksum) is None:
            msg = f"Invalid Tembo release manifest: invalid SHA-256 for {arch}"
            raise RuntimeError(msg)
        size = artifact.get("sizeBytes")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size not in _DMG_SIZE_RANGE
        ):
            msg = f"Invalid Tembo release manifest: implausible DMG size for {arch}"
            raise RuntimeError(msg)

        return nix_platform, json_utils.coerce_json_object(
            {
                "sha256": checksum,
                "sizeBytes": size,
                "url": immutable_url,
            },
            context=f"Tembo {nix_platform} metadata",
        )

    @classmethod
    def _parse_manifest(cls, payload: object) -> VersionInfo:
        root = _require_object(payload, context="manifest root")
        latest_by_platform = _require_object(
            root.get("latestByPlatform"),
            context="latestByPlatform",
        )
        release = _require_object(
            latest_by_platform.get("macos"),
            context="latestByPlatform.macos",
        )

        version = _require_string(
            release,
            "version",
            context="latestByPlatform.macos",
        )
        if _VERSION.fullmatch(version) is None:
            msg = f"Invalid Tembo release manifest: invalid version {version!r}"
            raise RuntimeError(msg)
        commit = _require_string(
            release,
            "gitSha",
            context="latestByPlatform.macos",
        )
        if _GIT_SHA.fullmatch(commit) is None:
            msg = f"Invalid Tembo release manifest: invalid gitSha {commit!r}"
            raise RuntimeError(msg)
        uploaded_at = _require_string(
            release,
            "uploadedAt",
            context="latestByPlatform.macos",
        )
        if _UPLOADED_AT.fullmatch(uploaded_at) is None:
            msg = f"Invalid Tembo release manifest: invalid uploadedAt {uploaded_at!r}"
            raise RuntimeError(msg)
        _require_exact_string(
            release,
            "platform",
            "macos",
            context="latestByPlatform.macos",
        )
        _require_exact_string(
            release,
            "arch",
            "multi",
            context="latestByPlatform.macos",
        )

        artifacts = _require_list(
            release.get("artifacts"),
            context="latestByPlatform.macos.artifacts",
        )
        if len(artifacts) != len(cls.PLATFORMS):
            msg = (
                "Invalid Tembo release manifest: expected exactly one DMG for each "
                "supported macOS architecture"
            )
            raise RuntimeError(msg)

        platform_info: dict[str, JsonObject] = {}
        for index, raw_artifact in enumerate(artifacts):
            nix_platform, artifact = cls._parse_artifact(
                raw_artifact,
                version=version,
                index=index,
            )
            if nix_platform in platform_info:
                msg = (
                    "Invalid Tembo release manifest: duplicate artifact for "
                    f"{nix_platform}"
                )
                raise RuntimeError(msg)
            platform_info[nix_platform] = artifact

        return VersionInfo(
            version=version,
            metadata=PlatformAPIMetadata(
                platform_info=platform_info,
                equality_fields={},
                commit=commit,
            ),
        )

    @classmethod
    def _metadata(cls, info: VersionInfo) -> PlatformAPIMetadata:
        metadata = PlatformAPIMetadata.from_metadata(
            info.metadata,
            context="tembo metadata",
        )
        if set(metadata.platform_info) != set(cls.PLATFORMS):
            msg = "Invalid Tembo updater metadata: incomplete platform map"
            raise RuntimeError(msg)
        if metadata.commit is None or _GIT_SHA.fullmatch(metadata.commit) is None:
            msg = "Invalid Tembo updater metadata: missing or invalid commit"
            raise RuntimeError(msg)
        return metadata

    @staticmethod
    def _metadata_field(
        metadata: PlatformAPIMetadata,
        platform: str,
        field: str,
    ) -> str:
        value = metadata.platform_info[platform].get(field)
        if not isinstance(value, str) or not value:
            msg = f"Invalid Tembo updater metadata: missing {field!r} for {platform}"
            raise RuntimeError(msg)
        return value

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the current macOS release without comparing release semvers."""
        payload = await fetch_json(session, self.MANIFEST_URL, config=self.config)
        return self._parse_manifest(payload)

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Return the vendor-published SHA-256 for each immutable DMG."""
        _ = session
        metadata = self._metadata(info)
        return {
            platform: self._metadata_field(metadata, platform, "sha256")
            for platform in self.PLATFORMS
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist immutable artifact URLs and the release commit."""
        metadata = self._metadata(info)
        urls = {
            platform: self._metadata_field(metadata, platform, "url")
            for platform in self.PLATFORMS
        }
        return self._build_result_with_urls(
            info,
            hashes,
            urls,
            commit=metadata.commit,
        )
