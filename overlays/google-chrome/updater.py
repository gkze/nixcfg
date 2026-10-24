"""Updater for the fully rolled-out Google Chrome stable releases."""

import asyncio
import platform as host_platform
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.events import EventSink, ignore_event
from lib.update.net import fetch_json, fetch_url
from lib.update.updaters import (
    UpdateContext,
    Updater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import MappingMetadata
from lib.update.updaters.vendor_feeds import (
    fetch_omaha_payload,
    parse_debian_packages,
    parse_omaha_updatecheck,
    sha256_hex_to_sri,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

_VERSION_HISTORY_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/{platform}/"
    "channels/stable/versions/all/releases"
    "?filter=endtime%3Dnone%2Cfraction%3D1"
    "&order_by=starttime%20desc&page_size=100"
)
_LINUX_PACKAGES_URL = (
    "https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages"
)
_LINUX_REPOSITORY_URL = "https://dl.google.com/linux/chrome/deb/"
_CHROME_APP_ID = "com.google.Chrome"
_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){3}")
_OS_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_DEBIAN_REVISION_PATTERN = re.compile(r"[A-Za-z0-9.+~]+")
_DARWIN_ARCHITECTURES = frozenset({"arm64", "x86_64"})
_DARWIN_ARTIFACT_PATTERN = re.compile(
    r"https://dl\.google\.com/release2/chrome/"
    r"[A-Za-z0-9_-]+_(?P<version>[0-9]+(?:\.[0-9]+){3})/"
    r"GoogleChrome-(?P=version)\.dmg"
)
_API_PLATFORM_SYSTEMS = {
    "mac_arm64": ("aarch64-darwin",),
    "mac": ("x86_64-darwin",),
    "linux": ("x86_64-linux",),
}


class ChromeLinuxRolloutLagError(RuntimeError):
    """Linux apt has not published the VersionHistory 100% Stable version yet."""


@dataclass(frozen=True, slots=True)
class _ChromeReleaseMetadata(MappingMetadata):
    """Artifact URLs and their platform-specific package versions."""

    asset_urls: dict[str, str]
    artifact_hashes: dict[str, str]
    platform_versions: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ResolvedOmahaArtifact:
    url: str
    sri_hash: str


def _full_rollout_version(payload: object, *, platform: str) -> str:
    """Return the sole active 100% Stable release in a VersionHistory response."""
    if not isinstance(payload, dict):
        msg = f"Unexpected Chrome VersionHistory payload for {platform}: {payload!r}"
        raise TypeError(msg)
    releases = payload.get("releases")
    if not isinstance(releases, list):
        msg = f"Missing Chrome VersionHistory releases for {platform}: {payload!r}"
        raise TypeError(msg)

    candidates: list[str] = []
    for release in releases:
        if not isinstance(release, dict):
            msg = (
                f"Unexpected Chrome VersionHistory release for {platform}: {release!r}"
            )
            raise TypeError(msg)
        version = release.get("version")
        if not isinstance(version, str) or _VERSION_PATTERN.fullmatch(version) is None:
            msg = f"Invalid Chrome version for {platform}: {release!r}"
            raise TypeError(msg)
        fraction = release.get("fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, int | float):
            msg = f"Invalid Chrome rollout fraction for {platform}: {release!r}"
            raise TypeError(msg)
        serving = release.get("serving")
        if not isinstance(serving, dict):
            msg = f"Invalid Chrome serving interval for {platform}: {release!r}"
            raise TypeError(msg)
        end_time = serving.get("endTime")
        if end_time is not None and not isinstance(end_time, str):
            msg = f"Invalid Chrome rollout end time for {platform}: {release!r}"
            raise TypeError(msg)
        if fraction == 1 and end_time is None:
            candidates.append(version)

    unique_candidates = tuple(dict.fromkeys(candidates))
    if not unique_candidates:
        msg = f"No active fully rolled-out Chrome Stable release for {platform}"
        raise RuntimeError(msg)
    if len(unique_candidates) != 1:
        msg = (
            f"Ambiguous active fully rolled-out Chrome Stable releases for {platform}: "
            f"{unique_candidates}"
        )
        raise RuntimeError(msg)
    return unique_candidates[0]


def _shared_darwin_version(versions: dict[str, str]) -> str:
    darwin_versions = {versions["mac_arm64"], versions["mac"]}
    if len(darwin_versions) != 1:
        msg = (
            "Chrome's universal DMG has divergent fully rolled-out Darwin "
            f"versions: {versions}"
        )
        raise RuntimeError(msg)
    return darwin_versions.pop()


def _darwin_host_identity() -> tuple[str, str]:
    """Return truthful OS compatibility fields for a Darwin Omaha request."""
    os_version = host_platform.mac_ver()[0]
    if _OS_VERSION_PATTERN.fullmatch(os_version) is None:
        msg = f"Cannot determine the Darwin host OS version: {os_version!r}"
        raise RuntimeError(msg)
    os_arch = host_platform.machine()
    if os_arch not in _DARWIN_ARCHITECTURES:
        msg = f"Unsupported Darwin host architecture for Chrome Omaha: {os_arch!r}"
        raise RuntimeError(msg)
    return os_version, os_arch


def _omaha_request(
    *,
    target_version: str,
    os_version: str,
    os_arch: str,
) -> dict[str, object]:
    """Build a protocol 3.1 request for one exact public-install artifact."""
    return {
        "request": {
            "@os": "mac",
            "@updater": "nixcfg",
            "acceptformat": "download",
            "app": [
                {
                    "appid": _CHROME_APP_ID,
                    "enabled": 1,
                    "installsource": "ondemand",
                    "version": "0",
                    "updatecheck": {"targetversionprefix": f"{target_version}$"},
                }
            ],
            "dedup": "cr",
            "ismachine": 1,
            "os": {
                "platform": "mac",
                "version": os_version,
                "arch": os_arch,
            },
            "protocol": "3.1",
            # Keep automated probes out of official installation metrics.
            "testsource": "prober",
            "updaterversion": "0",
        }
    }


def _object_list(value: object, *, context: str) -> list[dict[str, object]]:
    return [
        json_utils.as_object_dict(item, context=context)
        for item in json_utils.as_object_list(value, context=context)
    ]


def _parse_omaha_artifact(
    payload: bytes,
    *,
    expected_version: str,
) -> _ResolvedOmahaArtifact:
    """Return the immutable DMG URL when Omaha agrees with VersionHistory."""
    updatecheck = parse_omaha_updatecheck(
        payload, app_id=_CHROME_APP_ID, apps_key="app", label="Google Chrome"
    )
    manifest = json_utils.as_object_dict(
        updatecheck.get("manifest"),
        context="Google Chrome Omaha manifest",
    )
    manifest_version = json_utils.get_required_str(
        manifest,
        "version",
        context="Google Chrome Omaha manifest",
    )
    if manifest_version != expected_version:
        msg = (
            "Chrome Omaha artifact does not match the fully rolled-out Mac "
            f"version: expected {expected_version}, observed {manifest_version!r}"
        )
        raise RuntimeError(msg)

    packages_object = json_utils.as_object_dict(
        manifest.get("packages"),
        context="Google Chrome Omaha packages",
    )
    packages = _object_list(
        packages_object.get("package"),
        context="Google Chrome Omaha package list",
    )
    expected_package = f"GoogleChrome-{expected_version}.dmg"
    matching_packages = [
        package
        for package in packages
        if json_utils.get_required_str(
            package,
            "name",
            context="Google Chrome Omaha package",
        )
        == expected_package
    ]
    if len(matching_packages) != 1:
        msg = (
            "Chrome Omaha response contained "
            f"{len(matching_packages)} matching DMG packages"
        )
        raise RuntimeError(msg)
    package_hash = json_utils.get_required_str(
        matching_packages[0],
        "hash_sha256",
        context="Google Chrome Omaha package",
    )

    urls_object = json_utils.as_object_dict(
        updatecheck.get("urls"),
        context="Google Chrome Omaha URLs",
    )
    urls = _object_list(
        urls_object.get("url"),
        context="Google Chrome Omaha URL list",
    )
    codebases = {
        json_utils.get_required_str(
            url,
            "codebase",
            context="Google Chrome Omaha URL",
        )
        for url in urls
    }
    artifact_urls = {
        f"{codebase}{expected_package}"
        for codebase in codebases
        if _DARWIN_ARTIFACT_PATTERN.fullmatch(f"{codebase}{expected_package}")
    }
    if len(artifact_urls) != 1:
        msg = (
            "Chrome Omaha response contained "
            f"{len(artifact_urls)} immutable Google DMG URLs"
        )
        raise RuntimeError(msg)
    return _ResolvedOmahaArtifact(
        url=artifact_urls.pop(),
        sri_hash=sha256_hex_to_sri(
            package_hash,
            context="Google Chrome Omaha package",
        ),
    )


def _parse_linux_artifact(
    payload: bytes,
    *,
    expected_version: str,
) -> _ResolvedOmahaArtifact:
    """Return the immutable DEB URL matching VersionHistory and apt metadata."""
    candidates = parse_debian_packages(
        payload,
        package="google-chrome-stable",
        architecture="amd64",
        label="Google Chrome apt metadata",
    )
    if len(candidates) != 1:
        msg = (
            "Google Chrome apt metadata contained "
            f"{len(candidates)} stable amd64 packages"
        )
        raise RuntimeError(msg)
    package = candidates[0]
    chrome_version, separator, revision = package.version.rpartition("-")
    if not separator or _DEBIAN_REVISION_PATTERN.fullmatch(revision) is None:
        msg = (
            "Chrome apt package does not match the fully rolled-out Linux "
            f"version: expected {expected_version}, observed {package.version!r}"
        )
        raise RuntimeError(msg)
    if chrome_version != expected_version:
        msg = (
            "Chrome apt package does not match the fully rolled-out Linux "
            f"version: expected {expected_version}, observed {package.version!r}"
        )
        raise ChromeLinuxRolloutLagError(msg)
    return _ResolvedOmahaArtifact(
        url=f"{_LINUX_REPOSITORY_URL}{package.filename}",
        sri_hash=package.sri_hash,
    )


@register_updater
class GoogleChromeUpdater(Updater):
    """Resolve fully rolled-out Chrome artifacts and vendor-published hashes."""

    name = "google-chrome"
    PLATFORMS: ClassVar[dict[str, str]] = {
        system: api_platform
        for api_platform, systems in _API_PLATFORM_SYSTEMS.items()
        for system in systems
    }
    supported_platforms = tuple(
        system for system in PLATFORMS if system.endswith("-darwin")
    )

    def _metadata(self, info: VersionInfo) -> _ChromeReleaseMetadata:
        metadata = info.metadata
        if not isinstance(metadata, _ChromeReleaseMetadata):
            msg = "Missing Google Chrome artifact metadata"
            raise TypeError(msg)
        expected_platforms = set(self.PLATFORMS)
        if (
            set(metadata.asset_urls) != expected_platforms
            or set(metadata.artifact_hashes) != expected_platforms
            or set(metadata.platform_versions) != expected_platforms
        ):
            msg = "Incomplete Google Chrome platform artifact metadata"
            raise RuntimeError(msg)
        return metadata

    async def _fetch_darwin_artifact(
        self,
        session: aiohttp.ClientSession,
        *,
        expected_version: str,
    ) -> _ResolvedOmahaArtifact:
        os_version, os_arch = _darwin_host_identity()
        payload = await fetch_omaha_payload(
            session,
            app_id=_CHROME_APP_ID,
            body=_omaha_request(
                target_version=expected_version,
                os_version=os_version,
                os_arch=os_arch,
            ),
            config=self.config,
        )
        return _parse_omaha_artifact(payload, expected_version=expected_version)

    async def _fetch_linux_artifact(
        self,
        session: aiohttp.ClientSession,
        *,
        expected_version: str,
    ) -> _ResolvedOmahaArtifact:
        payload = await fetch_url(
            session,
            _LINUX_PACKAGES_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        return _parse_linux_artifact(payload, expected_version=expected_version)

    def _pinned_version_for_linux_rollout_lag(
        self,
        context: UpdateContext,
    ) -> VersionInfo:
        """Keep the published pin while Linux apt lags VersionHistory."""
        current = context.current
        hashes = None if current is None else current.hashes.mapping
        expected = set(self.PLATFORMS)
        if (
            current is None
            or current.version is None
            or current.urls is None
            or current.pins is None
            or hashes is None
            or set(current.urls) != expected
            or set(hashes) != expected
            or set(current.pins) != expected
        ):
            msg = (
                "Chrome apt package does not match the fully rolled-out Linux "
                "version, and no complete current pin is available"
            )
            raise ChromeLinuxRolloutLagError(msg)
        return VersionInfo(
            version=current.version,
            metadata=_ChromeReleaseMetadata(
                asset_urls=dict(current.urls),
                artifact_hashes=dict(hashes),
                platform_versions=dict(current.pins),
            ),
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Fetch the active 100% Stable baseline for each artifact platform."""

        async def _fetch_one(api_platform: str) -> tuple[str, str]:
            payload = await fetch_json(
                session,
                _VERSION_HISTORY_URL.format(platform=api_platform),
                config=self.config,
            )
            return api_platform, _full_rollout_version(
                payload,
                platform=api_platform,
            )

        versions = dict(
            await asyncio.gather(
                *(_fetch_one(platform) for platform in _API_PLATFORM_SYSTEMS)
            )
        )
        darwin_version = _shared_darwin_version(versions)
        try:
            darwin_artifact, linux_artifact = await asyncio.gather(
                self._fetch_darwin_artifact(
                    session,
                    expected_version=darwin_version,
                ),
                self._fetch_linux_artifact(
                    session,
                    expected_version=versions["linux"],
                ),
            )
        except ChromeLinuxRolloutLagError:
            return self._pinned_version_for_linux_rollout_lag(context)
        platform_versions = {
            system: versions[api_platform]
            for api_platform, systems in _API_PLATFORM_SYSTEMS.items()
            for system in systems
        }
        asset_urls = {
            "aarch64-darwin": darwin_artifact.url,
            "x86_64-darwin": darwin_artifact.url,
            "x86_64-linux": linux_artifact.url,
        }
        artifact_hashes = {
            "aarch64-darwin": darwin_artifact.sri_hash,
            "x86_64-darwin": darwin_artifact.sri_hash,
            "x86_64-linux": linux_artifact.sri_hash,
        }
        return VersionInfo(
            version=platform_versions["aarch64-darwin"],
            metadata=_ChromeReleaseMetadata(
                asset_urls=asset_urls,
                artifact_hashes=artifact_hashes,
                platform_versions=platform_versions,
            ),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Return checksums published alongside the immutable artifacts."""
        _ = (session, context, emit)
        metadata = self._metadata(info)
        return dict(metadata.artifact_hashes)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist URLs, hashes, and exact package versions as one identity."""
        metadata = self._metadata(info)
        return self._build_result_with_urls(info, hashes, dict(metadata.asset_urls))

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        """Skip only when the complete vendor-published identity is unchanged."""
        current = context.current
        if current is None:
            return False
        metadata = self._metadata(info)
        candidate = self.build_result(info, metadata.artifact_hashes)
        return (
            current.version == candidate.version
            and current.pins == candidate.pins
            and current.urls == candidate.urls
            and current.hashes.equivalent_to(candidate.hashes)
        )

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the artifact version associated with every Nix platform."""
        metadata = self._metadata(info)
        return dict(metadata.platform_versions)
