"""Concrete source updater implementations.

This module contains all the specific updater classes for each source
defined in sources.json. These updaters are automatically registered
via the `name` class attribute.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import TYPE_CHECKING

from lib import (
    EventCollector,
    EventStream,
    HashCollection,
    HashEntry,
    PlatformMapping,
    SourceEntry,
    UpdateEvent,
    VersionInfo,
    VSCODE_PLATFORMS,
    compute_fixed_output_hash,
    fetch_github_api,
    fetch_json,
    fetch_url,
    request,
    verify_platform_versions,
)
from updaters.base import (
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    Updater,
    cargo_vendor_updater,
    deno_deps_updater,
    github_raw_file_updater,
    go_vendor_updater,
    npm_deps_updater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.models import SourceHashes


# =============================================================================
# GitHub Raw File Updaters (via factory)
# =============================================================================

github_raw_file_updater(
    "gitui-key-config",
    owner="extrawurst",
    repo="gitui",
    path="vim_style_key_config.ron",
)

github_raw_file_updater(
    "homebrew-zsh-completion",
    owner="Homebrew",
    repo="brew",
    path="completions/zsh/_brew",
)


# =============================================================================
# Go Vendor Hash Updaters (via factory)
# =============================================================================

go_vendor_updater("axiom-cli", subpackages=["cmd/axiom"])
go_vendor_updater("beads", subpackages=["cmd/bd"], proxy_vendor=True)
go_vendor_updater("crush")
go_vendor_updater("gogcli", subpackages=["cmd/gog"])


# =============================================================================
# Cargo Vendor Hash Updaters (via factory)
# =============================================================================

cargo_vendor_updater("codex", subdir="codex-rs")


# =============================================================================
# npm Deps Hash Updaters (via factory)
# =============================================================================

npm_deps_updater("gemini-cli")


# =============================================================================
# Deno Deps Hash Updaters (via factory)
# =============================================================================

deno_deps_updater("linear-cli")


# =============================================================================
# Concrete Updater Classes
# =============================================================================


class GoogleChromeUpdater(DownloadHashUpdater):
    """Update Google Chrome to latest stable version."""

    name = "google-chrome"
    platforms = PlatformMapping(
        {
            "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
            "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
        }
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        data = await fetch_json(
            session,
            "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1",
        )
        return VersionInfo(version=data[0]["version"], metadata={})


class DataGripUpdater(ChecksumProvidedUpdater):
    """Update DataGrip to latest stable version."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    # nix platform -> JetBrains download key
    platforms = PlatformMapping(
        {
            "aarch64-darwin": "macM1",
            "aarch64-linux": "linuxARM64",
            "x86_64-linux": "linux",
        }
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        data = await fetch_json(session, self.API_URL)
        release = data["DG"][0]
        return VersionInfo(version=release["version"], metadata={"release": release})

    async def fetch_checksums(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> dict[str, str]:
        release = info.metadata["release"]
        checksums = {}
        for nix_platform in self.platforms.platforms:
            jetbrains_key = self.platforms.mapping[nix_platform]
            checksum_url = release["downloads"][jetbrains_key]["checksumLink"]
            # Format: "hexhash *filename"
            payload = await fetch_url(session, checksum_url)
            hex_hash = payload.decode().split()[0]
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        release = info.metadata["release"]
        urls = {
            nix_platform: release["downloads"][self.platforms.mapping[nix_platform]][
                "link"
            ]
            for nix_platform in self.platforms.platforms
        }
        return self._build_result_with_urls(info, hashes, urls)


class ChatGPTUpdater(DownloadHashUpdater):
    """Update ChatGPT desktop app to latest version using Sparkle appcast."""

    name = "chatgpt"

    APPCAST_URL = (
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    )

    # Both darwin platforms use the same universal binary
    platforms = PlatformMapping(
        {
            "aarch64-darwin": "darwin",
            "x86_64-darwin": "darwin",
        }
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        """Fetch version and download URL from Sparkle appcast XML."""
        # Use Sparkle user agent to avoid 403
        xml_payload = await fetch_url(
            session,
            self.APPCAST_URL,
            user_agent="Sparkle/2.0",
        )
        xml_data = xml_payload.decode()

        root = ET.fromstring(xml_data)
        # Get the first (latest) item
        item = root.find(".//item")
        if item is None:
            raise RuntimeError("No items found in appcast")

        # Sparkle namespace
        ns = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}

        version_elem = item.find("sparkle:shortVersionString", ns)
        if version_elem is None or version_elem.text is None:
            raise RuntimeError("No version found in appcast")

        enclosure = item.find("enclosure")
        if enclosure is None:
            raise RuntimeError("No enclosure found in appcast")

        url = enclosure.get("url")
        if url is None:
            raise RuntimeError("No URL found in enclosure")

        return VersionInfo(version=version_elem.text, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["url"]

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        return self._build_result_with_urls(
            info, hashes, {"darwin": info.metadata["url"]}
        )


class DroidUpdater(ChecksumProvidedUpdater):
    """Update Factory Droid CLI to latest version."""

    name = "droid"

    INSTALL_SCRIPT_URL = "https://app.factory.ai/cli"
    BASE_URL = "https://downloads.factory.ai/factory-cli/releases"

    # nix platform -> (os, arch)
    _PLATFORM_INFO: dict[str, tuple[str, str]] = {
        "aarch64-darwin": ("darwin", "arm64"),
        "x86_64-darwin": ("darwin", "x64"),
        "aarch64-linux": ("linux", "arm64"),
        "x86_64-linux": ("linux", "x64"),
    }
    platforms = PlatformMapping({p: "" for p in _PLATFORM_INFO})

    def _download_url(self, nix_platform: str, version: str) -> str:
        os_name, arch = self._PLATFORM_INFO[nix_platform]
        return f"{self.BASE_URL}/{version}/{os_name}/{arch}/droid"

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        """Parse version from the install script."""
        script = await fetch_url(session, self.INSTALL_SCRIPT_URL)
        match = re.search(r'VER="([^"]+)"', script.decode())
        if not match:
            raise RuntimeError(
                "Could not parse version from Factory CLI install script"
            )
        return VersionInfo(version=match.group(1), metadata={})

    async def fetch_checksums(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> dict[str, str]:
        checksums = {}
        for nix_platform in self._PLATFORM_INFO:
            sha_url = f"{self._download_url(nix_platform, info.version)}.sha256"
            payload = await fetch_url(session, sha_url)
            hex_hash = payload.decode().strip()
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        urls = {p: self._download_url(p, info.version) for p in self._PLATFORM_INFO}
        return self._build_result_with_urls(info, hashes, urls)


class ConductorUpdater(DownloadHashUpdater):
    """Update Conductor to latest version from CrabNebula CDN."""

    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    platforms = PlatformMapping(
        {"aarch64-darwin": "dmg-aarch64", "x86_64-darwin": "dmg-x86_64"}
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        url = f"{self.BASE_URL}/dmg-aarch64"
        _payload, headers = await request(session, url, method="HEAD")
        match = re.search(
            r"Conductor_([0-9.]+)_", headers.get("Content-Disposition", "")
        )
        if not match:
            raise RuntimeError("Could not parse version from Content-Disposition")
        return VersionInfo(version=match.group(1), metadata={})


class SculptorUpdater(DownloadHashUpdater):
    """Update Sculptor (uses Last-Modified header as version since no API exists)."""

    name = "sculptor"
    BASE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor"
    platforms = PlatformMapping(
        {
            "aarch64-darwin": "Sculptor.dmg",
            "x86_64-darwin": "Sculptor-x86_64.dmg",
            "x86_64-linux": "AppImage/x64/Sculptor.AppImage",
        }
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        url = f"{self.BASE_URL}/Sculptor.dmg"
        _payload, headers = await request(session, url, method="HEAD")
        last_modified = headers.get("Last-Modified", "")
        if not last_modified:
            raise RuntimeError("No Last-Modified header from Sculptor download")
        try:
            dt = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
            version = dt.strftime("%Y-%m-%d")
        except ValueError:
            version = last_modified[:10]
        return VersionInfo(version=version, metadata={})


class PlatformAPIUpdater(ChecksumProvidedUpdater):
    """Base for updaters that fetch per-platform info from an API."""

    VERSION_KEY: str = "version"  # Key for version in API response
    CHECKSUM_KEY: str | None = None  # Key for checksum in API response (if provided)

    def _api_url(self, api_platform: str) -> str:
        """Return API URL for a platform. Override in subclass."""
        raise NotImplementedError

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform. Override in subclass."""
        raise NotImplementedError

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        platform_info = {
            nix_plat: await fetch_json(
                session, self._api_url(self.platforms.mapping[nix_plat])
            )
            for nix_plat in self.platforms.platforms
        }
        versions = {p: info[self.VERSION_KEY] for p, info in platform_info.items()}
        version = verify_platform_versions(versions, self.name)
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    async def fetch_checksums(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> dict[str, str]:
        if not self.CHECKSUM_KEY:
            raise NotImplementedError("No CHECKSUM_KEY defined")
        platform_info = info.metadata["platform_info"]
        return {
            p: platform_info[p][self.CHECKSUM_KEY] for p in self.platforms.platforms
        }

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        urls = {
            nix_plat: self._download_url(self.platforms.mapping[nix_plat], info)
            for nix_plat in self.platforms.platforms
        }
        return self._build_result_with_urls(info, hashes, urls)


class VSCodeInsidersUpdater(PlatformAPIUpdater):
    """Update VS Code Insiders to latest version."""

    name = "vscode-insiders"
    platforms = PlatformMapping(VSCODE_PLATFORMS)
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, api_platform: str) -> str:
        return f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        return f"https://update.code.visualstudio.com/{info.version}/{api_platform}/insider"


class SentryCliUpdater(Updater):
    """Update sentry-cli to latest GitHub release.

    Builds from source using fetchFromGitHub with postFetch to strip .xcarchive
    test fixtures (macOS code-signed bundles that break nix-store --optimise).
    """

    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        data = await fetch_github_api(
            session, f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest"
        )
        return VersionInfo(version=data["tag_name"], metadata={})

    def _src_nix_expr(self, version: str, hash_value: str = "pkgs.lib.fakeHash") -> str:
        """Build nix expression for fetchFromGitHub with xcarchive filtering."""
        return (
            f"pkgs.fetchFromGitHub {{\n"
            f'  owner = "{self.GITHUB_OWNER}";\n'
            f'  repo = "{self.GITHUB_REPO}";\n'
            f'  tag = "{version}";\n'
            f"  hash = {hash_value};\n"
            f'  postFetch = "{self.XCARCHIVE_FILTER}";\n'
            f"}}"
        )

    def _build_nix_expr(self, body: str) -> str:
        """Build a nix expression with nixpkgs prelude."""
        from lib.nix import FlakeLock

        lock = FlakeLock.load()
        return f"""
          let
            pkgs = {lock.nixpkgs_expr()};
          in
            {body}
        """

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        # Step 1: Compute source hash (fetchFromGitHub with xcarchive filtering)
        src_hash_collector: EventCollector[str] = EventCollector()
        async for event in src_hash_collector.collect(
            compute_fixed_output_hash(
                self.name,
                self._build_nix_expr(self._src_nix_expr(info.version)),
            )
        ):
            yield event
        src_hash = src_hash_collector.require_value("Missing srcHash output")

        # Step 2: Compute cargo vendor hash using the filtered source
        src_expr = self._src_nix_expr(info.version, f'"{src_hash}"')
        cargo_hash_collector: EventCollector[str] = EventCollector()
        async for event in cargo_hash_collector.collect(
            compute_fixed_output_hash(
                self.name,
                self._build_nix_expr(
                    f"pkgs.rustPlatform.fetchCargoVendor {{\n"
                    f"  src = {src_expr};\n"
                    f"  hash = pkgs.lib.fakeHash;\n"
                    f"}}"
                ),
            )
        ):
            yield event
        cargo_hash = cargo_hash_collector.require_value("Missing cargoHash output")

        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("fetchFromGitHub", "srcHash", src_hash),
                HashEntry.create("fetchCargoVendor", "cargoHash", cargo_hash),
            ],
        )

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
        )


class CodeCursorUpdater(DownloadHashUpdater):
    """Update Cursor editor to latest stable version."""

    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    platforms = PlatformMapping(
        {
            "aarch64-darwin": "darwin-arm64",
            "x86_64-darwin": "darwin-x64",
            "aarch64-linux": "linux-arm64",
            "x86_64-linux": "linux-x64",
        }
    )

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        platform_info = {
            nix_plat: await fetch_json(
                session,
                f"{self.API_BASE}?platform={self.platforms.mapping[nix_plat]}&releaseTrack=stable",
            )
            for nix_plat in self.platforms.platforms
        }
        versions = {p: info["version"] for p, info in platform_info.items()}
        commits = {p: info["commitSha"] for p, info in platform_info.items()}
        version = verify_platform_versions(versions, "Cursor")
        commit = verify_platform_versions(commits, "Cursor commit")
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "platform_info": platform_info},
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["platform_info"][platform]["downloadUrl"]

    def build_result(self, info: VersionInfo, hashes: "SourceHashes") -> SourceEntry:
        urls = {p: self.get_download_url(p, info) for p in self.platforms.platforms}
        return self._build_result_with_urls(
            info, hashes, urls, commit=info.metadata["commit"]
        )
