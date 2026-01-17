#!/usr/bin/env python3
"""Update source versions and hashes in sources.json.

Usage:
    ./update.py <source>       Update a specific source
    ./update.py --all          Update all sources
    ./update.py --list         List available sources

Sources are defined with custom update logic for fetching latest versions
and computing hashes from upstream release channels.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


# =============================================================================
# Configuration
# =============================================================================


def get_sources_file() -> Path:
    """Resolve sources.json location (handles nix store paths)."""
    script_path = Path(__file__)
    if "/nix/store" in str(script_path):
        return Path.cwd() / "sources.json"
    return script_path.parent / "sources.json"


SOURCES_FILE = get_sources_file()


# =============================================================================
# Utilities
# =============================================================================


def fetch_url(url: str, user_agent: str | None = None) -> bytes:
    """Fetch content from a URL with optional user agent."""
    req = urllib.request.Request(url)
    if user_agent:
        req.add_header("User-Agent", user_agent)
    with urllib.request.urlopen(req) as response:
        return response.read()


def fetch_json(url: str) -> dict:
    """Fetch and parse JSON from a URL."""
    return json.loads(fetch_url(url).decode())


def _nix_hash_to_sri(hash_value: str) -> str:
    """Convert any nix hash format (base32, hex) to SRI format."""
    result = subprocess.run(
        ["nix", "hash", "convert", "--hash-algo", "sha256", "--to", "sri", hash_value],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def compute_sri_hash(url: str) -> str:
    """Compute SRI hash for a URL using nix-prefetch-url."""
    result = subprocess.run(
        ["nix-prefetch-url", "--type", "sha256", url],
        capture_output=True,
        text=True,
        check=True,
    )
    base32_hash = result.stdout.strip().split("\n")[-1]
    return _nix_hash_to_sri(base32_hash)


def hex_to_sri(hex_hash: str) -> str:
    """Convert hex sha256 hash to SRI format."""
    return _nix_hash_to_sri(hex_hash)


# =============================================================================
# Updater Base Class
# =============================================================================

# Registry populated automatically via __init_subclass__
UPDATERS: dict[str, type["Updater"]] = {}


def _print_hash(platform: str, sri: str, note: str | None = None) -> None:
    """Print hash in consistent format."""
    if note:
        print(f"  {platform}: {note}")
    else:
        print(f"  {platform}: {sri[:32]}...")


@dataclass
class VersionInfo:
    """Version and metadata fetched from upstream."""

    version: str
    metadata: dict  # Updater-specific data (URLs, checksums, release info, etc.)


@dataclass
class UpdateResult:
    """Result of an update check."""

    version: str
    hashes: dict[str, str]
    urls: dict[str, str] | None = None  # Optional platform -> URL mapping


class Updater(ABC):
    """Base class for source updaters. Subclasses auto-register via `name` attribute."""

    name: str  # Source name (e.g., "google-chrome")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name"):
            UPDATERS[cls.name] = cls

    @abstractmethod
    def fetch_latest(self) -> VersionInfo:
        """Fetch the latest version and any metadata needed for hashes."""

    @abstractmethod
    def fetch_hashes(self, info: VersionInfo) -> dict[str, str]:
        """Fetch hashes for all platforms. Returns {nix_platform: sri_hash}."""

    def build_result(self, info: VersionInfo, hashes: dict[str, str]) -> UpdateResult:
        """Build UpdateResult from version info and hashes. Override to add URLs."""
        return UpdateResult(version=info.version, hashes=hashes)

    def update(self, current: dict) -> UpdateResult | None:
        """Check for updates. Returns UpdateResult or None if up-to-date."""
        print(f"Fetching latest {self.name} version...")
        info = self.fetch_latest()

        print(f"Latest version: {info.version}")
        if current.get("version") == info.version:
            print("Already at latest version")
            return None

        print("Fetching hashes for all platforms...")
        hashes = self.fetch_hashes(info)
        return self.build_result(info, hashes)


# =============================================================================
# Specialized Updater Base Classes
# =============================================================================


class ChecksumProvidedUpdater(Updater):
    """Base for sources that provide checksums in their API (no download needed)."""

    PLATFORMS: dict[str, str]  # nix_platform -> api_key

    @abstractmethod
    def fetch_checksums(self, info: VersionInfo) -> dict[str, str]:
        """Return {nix_platform: hex_hash} from API metadata."""

    def fetch_hashes(self, info: VersionInfo) -> dict[str, str]:
        """Convert API checksums to SRI format."""
        hashes = {}
        for platform, hex_hash in self.fetch_checksums(info).items():
            sri = hex_to_sri(hex_hash)
            hashes[platform] = sri
            _print_hash(platform, sri)
        return hashes


class DownloadHashUpdater(Updater):
    """Base for sources requiring download to compute hash, with URL deduplication."""

    PLATFORMS: dict[str, str]  # nix_platform -> download_url or template

    @abstractmethod
    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform."""

    def fetch_hashes(self, info: VersionInfo) -> dict[str, str]:
        """Compute hashes by downloading, deduplicating identical URLs."""
        hashes = {}
        seen_urls: dict[str, str] = {}

        for platform in self.PLATFORMS:
            url = self.get_download_url(platform, info)
            if url in seen_urls:
                hashes[platform] = seen_urls[url]
                _print_hash(platform, seen_urls[url], "(same as above)")
            else:
                sri = compute_sri_hash(url)
                hashes[platform] = sri
                seen_urls[url] = sri
                _print_hash(platform, sri)

        return hashes


# =============================================================================
# Source Updaters
# =============================================================================


class GoogleChromeUpdater(DownloadHashUpdater):
    """Update Google Chrome to latest stable version."""

    name = "google-chrome"

    # nix platform -> download url
    PLATFORMS = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    def fetch_latest(self) -> VersionInfo:
        url = "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1"
        data = fetch_json(url)
        return VersionInfo(version=data[0]["version"], metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return self.PLATFORMS[platform]


class DataGripUpdater(ChecksumProvidedUpdater):
    """Update DataGrip to latest stable version."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    # nix platform -> JetBrains download key
    PLATFORMS = {
        "aarch64-darwin": "macM1",
        "x86_64-darwin": "mac",
        "aarch64-linux": "linuxARM64",
        "x86_64-linux": "linux",
    }

    def fetch_latest(self) -> VersionInfo:
        data = fetch_json(self.API_URL)
        release = data["DG"][0]
        return VersionInfo(version=release["version"], metadata={"release": release})

    def fetch_checksums(self, info: VersionInfo) -> dict[str, str]:
        release = info.metadata["release"]
        checksums = {}
        for nix_platform, jb_key in self.PLATFORMS.items():
            checksum_url = release["downloads"][jb_key]["checksumLink"]
            with urllib.request.urlopen(checksum_url) as response:
                # Format: "hexhash *filename"
                hex_hash = response.read().decode().split()[0]
            checksums[nix_platform] = hex_hash
        return checksums


class ChatGPTUpdater(DownloadHashUpdater):
    """Update ChatGPT desktop app to latest version using Sparkle appcast."""

    name = "chatgpt"

    APPCAST_URL = (
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    )

    # Both darwin platforms use the same universal binary
    PLATFORMS = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    def fetch_latest(self) -> VersionInfo:
        """Fetch version and download URL from Sparkle appcast XML."""
        import xml.etree.ElementTree as ET

        # Use Sparkle user agent to avoid 403
        xml_data = fetch_url(self.APPCAST_URL, user_agent="Sparkle/2.0").decode()

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
        print(f"  URL: {info.metadata['url']}")
        return info.metadata["url"]

    def build_result(self, info: VersionInfo, hashes: dict[str, str]) -> UpdateResult:
        """Include the versioned URL in the result."""
        return UpdateResult(
            version=info.version,
            hashes=hashes,
            urls={"darwin": info.metadata["url"]},
        )


class ConductorUpdater(DownloadHashUpdater):
    """Update Conductor to latest version from CrabNebula CDN."""

    name = "conductor"

    # nix platform -> CrabNebula platform
    PLATFORMS = {
        "aarch64-darwin": "dmg-aarch64",
        "x86_64-darwin": "dmg-x86_64",
    }

    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"

    def fetch_latest(self) -> VersionInfo:
        """Fetch version from Content-Disposition header of the download."""
        import re

        # Follow redirects to get the final Content-Disposition header
        url = f"{self.BASE_URL}/dmg-aarch64"
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req) as response:
            # Get final URL after redirects
            final_url = response.geturl()

        # Fetch headers from the final URL
        req = urllib.request.Request(final_url, method="HEAD")
        with urllib.request.urlopen(req) as response:
            disposition = response.headers.get("Content-Disposition", "")

        # Parse version from filename like "Conductor_0.31.1_aarch64.dmg"
        match = re.search(r"Conductor_([0-9.]+)_", disposition)
        if not match:
            raise RuntimeError(f"Could not parse version from: {disposition}")

        return VersionInfo(version=match.group(1), metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"


class VSCodeInsidersUpdater(ChecksumProvidedUpdater):
    """Update VS Code Insiders to latest version."""

    name = "vscode-insiders"

    # nix platform -> vscode api platform
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "aarch64-linux": "linux-arm64",
        "x86_64-darwin": "darwin",
        "x86_64-linux": "linux-x64",
    }

    def _fetch_platform_info(self, api_platform: str) -> dict:
        url = f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"
        return fetch_json(url)

    def fetch_latest(self) -> VersionInfo:
        # Fetch info for all platforms upfront to avoid repeated API calls
        platform_info = {}
        for nix_platform, api_platform in self.PLATFORMS.items():
            platform_info[nix_platform] = self._fetch_platform_info(api_platform)
        # Use first platform's version (all should be the same)
        version = platform_info[next(iter(self.PLATFORMS))]["productVersion"]
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    def fetch_checksums(self, info: VersionInfo) -> dict[str, str]:
        platform_info = info.metadata["platform_info"]
        return {
            platform: platform_info[platform]["sha256hash"]
            for platform in self.PLATFORMS
        }


# =============================================================================
# Main
# =============================================================================


def load_sources() -> dict:
    if SOURCES_FILE.exists():
        return json.loads(SOURCES_FILE.read_text())
    return {}


def save_sources(sources: dict):
    SOURCES_FILE.write_text(json.dumps(sources, indent=2) + "\n")


def update_source(name: str, sources: dict) -> bool:
    """Update a single source. Returns True if updated."""
    if name not in UPDATERS:
        print(f"Error: Unknown source '{name}'")
        print(f"Available sources: {', '.join(UPDATERS.keys())}")
        return False

    print(f"\n{'=' * 60}")
    print(f"Updating {name}")
    print("=" * 60)

    current = sources.get(name, {})
    updater_cls = UPDATERS[name]

    try:
        result = updater_cls().update(current)
        if result is None:
            return False
        entry = {"version": result.version, "hashes": result.hashes}
        if result.urls:
            entry["urls"] = result.urls
        sources[name] = entry
        return True
    except Exception as e:
        print(f"Error updating {name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Update source versions and hashes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument("source", nargs="?", help="Source to update")
    parser.add_argument("--all", action="store_true", help="Update all sources")
    parser.add_argument("--list", action="store_true", help="List available sources")
    args = parser.parse_args()

    if args.list:
        print("Available sources:")
        for name in UPDATERS:
            print(f"  {name}")
        return

    if not args.source and not args.all:
        parser.print_help()
        return

    sources = load_sources()
    updated = False

    if args.all:
        for name in UPDATERS:
            if update_source(name, sources):
                updated = True
    else:
        updated = update_source(args.source, sources)

    if updated:
        save_sources(sources)
        print(f"\nUpdated {SOURCES_FILE}")
        print("Run: nh darwin switch --no-nom .")
    else:
        print("\nNo updates needed.")


if __name__ == "__main__":
    main()
