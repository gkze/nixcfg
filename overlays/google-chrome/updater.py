"""Updater for the fully rolled-out Google Chrome stable releases."""

import asyncio
import base64
import json
import platform as host_platform
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, ClassVar

import aiohttp

from lib import json_utils
from lib.update.events import UpdateEvent
from lib.update.net import HTTP_BAD_REQUEST, fetch_json, fetch_url
from lib.update.updaters import (
    UpdateContext,
    Updater,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry, SourceHashes
    from lib.update.events import EventStream

_VERSION_HISTORY_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/{platform}/"
    "channels/stable/versions/all/releases"
    "?filter=endtime%3Dnone%2Cfraction%3D1"
    "&order_by=starttime%20desc&page_size=100"
)
_OMAHA_URL = "https://update.googleapis.com/service/update2/json"
_LINUX_PACKAGES_URL = (
    "https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages"
)
_LINUX_REPOSITORY_URL = "https://dl.google.com/linux/chrome/deb/"
_CHROME_APP_ID = "com.google.Chrome"
_ANTI_XSSI_PREFIX = b")]}'\n"
_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){3}")
_OS_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_DEBIAN_REVISION_PATTERN = re.compile(r"[A-Za-z0-9.+~]+")
_DEBIAN_PATH_COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9._+~-]+")
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
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


@dataclass(frozen=True, slots=True)
class _ChromeReleaseMetadata:
    """Artifact URLs and their platform-specific package versions."""

    asset_urls: dict[str, str]
    artifact_hashes: dict[str, str]
    platform_versions: dict[str, str]


@dataclass(frozen=True, slots=True)
class _ResolvedArtifact:
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


def _sri_sha256(value: str, *, context: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        msg = f"Invalid SHA-256 in {context}: {value!r}"
        raise RuntimeError(msg)
    digest = bytes.fromhex(value)
    return f"sha256-{base64.b64encode(digest).decode()}"


def _parse_omaha_artifact(
    payload: bytes,
    *,
    expected_version: str,
) -> _ResolvedArtifact:
    """Return the immutable DMG URL when Omaha agrees with VersionHistory."""
    if not payload.startswith(_ANTI_XSSI_PREFIX):
        msg = "Google Chrome Omaha response omitted its anti-XSSI prefix"
        raise RuntimeError(msg)
    try:
        payload_value = json.loads(payload.removeprefix(_ANTI_XSSI_PREFIX))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "Google Chrome Omaha response was not valid JSON"
        raise RuntimeError(msg) from exc

    payload_object = json_utils.as_object_dict(
        payload_value,
        context="Google Chrome Omaha response",
    )
    response = json_utils.as_object_dict(
        payload_object.get("response"),
        context="Google Chrome Omaha response.response",
    )
    apps = _object_list(
        response.get("app"),
        context="Google Chrome Omaha response apps",
    )
    matching_apps = [
        app
        for app in apps
        if json_utils.get_required_str(
            app,
            "appid",
            context="Google Chrome Omaha app",
        )
        == _CHROME_APP_ID
    ]
    if len(matching_apps) != 1:
        msg = f"Chrome Omaha response contained {len(matching_apps)} matching apps"
        raise RuntimeError(msg)
    app = matching_apps[0]
    app_status = json_utils.get_required_str(
        app,
        "status",
        context="Google Chrome Omaha app",
    )
    if app_status != "ok":
        msg = f"Chrome Omaha app returned status {app_status!r}"
        raise RuntimeError(msg)
    updatecheck = json_utils.as_object_dict(
        app.get("updatecheck"),
        context="Google Chrome Omaha updatecheck",
    )
    update_status = json_utils.get_required_str(
        updatecheck,
        "status",
        context="Google Chrome Omaha updatecheck",
    )
    if update_status != "ok":
        msg = f"Chrome Omaha updatecheck returned status {update_status!r}"
        raise RuntimeError(msg)
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
    return _ResolvedArtifact(
        url=artifact_urls.pop(),
        sri_hash=_sri_sha256(
            package_hash,
            context="Google Chrome Omaha package",
        ),
    )


def _parse_debian_fields(stanza: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in stanza.splitlines():
        if not line or line[0].isspace():
            continue
        name, separator, value = line.partition(":")
        if not separator:
            msg = f"Malformed Google Chrome apt metadata line: {line!r}"
            raise RuntimeError(msg)
        fields.setdefault(name, []).append(value.strip())
    return fields


def _required_debian_field(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name, [])
    if len(values) != 1 or not values[0]:
        msg = f"Google Chrome apt metadata has invalid {name!r} field"
        raise RuntimeError(msg)
    return values[0]


def _parse_linux_artifact(
    payload: bytes,
    *,
    expected_version: str,
) -> _ResolvedArtifact:
    """Return the immutable DEB URL matching VersionHistory and apt metadata."""
    try:
        text = payload.decode()
    except UnicodeDecodeError as exc:
        msg = "Google Chrome apt Packages metadata is not UTF-8"
        raise RuntimeError(msg) from exc
    stanzas = [
        _parse_debian_fields(stanza)
        for stanza in re.split(r"\r?\n\r?\n+", text.strip())
        if stanza.strip()
    ]
    candidates = [
        fields
        for fields in stanzas
        if fields.get("Package") == ["google-chrome-stable"]
        and fields.get("Architecture") == ["amd64"]
    ]
    if len(candidates) != 1:
        msg = (
            "Google Chrome apt metadata contained "
            f"{len(candidates)} stable amd64 packages"
        )
        raise RuntimeError(msg)
    fields = candidates[0]
    package_version = _required_debian_field(fields, "Version")
    chrome_version, separator, revision = package_version.rpartition("-")
    if (
        not separator
        or chrome_version != expected_version
        or _DEBIAN_REVISION_PATTERN.fullmatch(revision) is None
    ):
        msg = (
            "Chrome apt package does not match the fully rolled-out Linux "
            f"version: expected {expected_version}, observed {package_version!r}"
        )
        raise RuntimeError(msg)

    filename = _required_debian_field(fields, "Filename")
    path = PurePosixPath(filename)
    expected_name = f"google-chrome-stable_{package_version}_amd64.deb"
    if (
        path.is_absolute()
        or path.as_posix() != filename
        or not path.parts
        or path.parts[0] != "pool"
        or path.name != expected_name
        or any(
            component in {".", ".."}
            or _DEBIAN_PATH_COMPONENT_PATTERN.fullmatch(component) is None
            for component in path.parts
        )
    ):
        msg = f"Google Chrome apt metadata has invalid Filename: {filename!r}"
        raise RuntimeError(msg)
    package_hash = _required_debian_field(fields, "SHA256")
    return _ResolvedArtifact(
        url=f"{_LINUX_REPOSITORY_URL}{filename}",
        sri_hash=_sri_sha256(
            package_hash,
            context="Google Chrome apt package",
        ),
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
    ) -> _ResolvedArtifact:
        os_version, os_arch = _darwin_host_identity()
        headers = {
            "User-Agent": self.config.default_user_agent,
            "Content-Type": "application/json",
            "X-Goog-Update-Interactivity": "fg",
            "X-Goog-Update-AppId": _CHROME_APP_ID,
            "X-Goog-Update-Updater": "nixcfg-0",
        }
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "POST",
            _OMAHA_URL,
            headers=headers,
            json=_omaha_request(
                target_version=expected_version,
                os_version=os_version,
                os_arch=os_arch,
            ),
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            payload = await response.read()
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    "Google Chrome Omaha request failed with "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
        return _parse_omaha_artifact(payload, expected_version=expected_version)

    async def _fetch_linux_artifact(
        self,
        session: aiohttp.ClientSession,
        *,
        expected_version: str,
    ) -> _ResolvedArtifact:
        payload = await fetch_url(
            session,
            _LINUX_PACKAGES_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        return _parse_linux_artifact(payload, expected_version=expected_version)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
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
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Return checksums published alongside the immutable artifacts."""
        _ = (session, context)
        metadata = self._metadata(info)
        yield UpdateEvent.value(self.name, dict(metadata.artifact_hashes))

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist URLs, hashes, and exact package versions as one identity."""
        metadata = self._metadata(info)
        return self._build_result_with_urls(info, hashes, dict(metadata.asset_urls))

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Skip only when the complete vendor-published identity is unchanged."""
        current = context.current if isinstance(context, UpdateContext) else context
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
