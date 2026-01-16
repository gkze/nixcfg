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


def fetch_json(url: str) -> dict:
    """Fetch and parse JSON from a URL."""
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())


def compute_sri_hash(url: str) -> str:
    """Compute SRI hash for a URL using nix-prefetch-url."""
    result = subprocess.run(
        ["nix-prefetch-url", "--type", "sha256", url],
        capture_output=True,
        text=True,
        check=True,
    )
    base32_hash = result.stdout.strip().split("\n")[-1]

    result = subprocess.run(
        ["nix", "hash", "convert", "--hash-algo", "sha256", "--to", "sri", base32_hash],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def hex_to_sri(hex_hash: str) -> str:
    """Convert hex sha256 hash to SRI format."""
    result = subprocess.run(
        ["nix", "hash", "convert", "--hash-algo", "sha256", "--to", "sri", hex_hash],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# =============================================================================
# Updater Base Class
# =============================================================================

# Registry populated automatically via __init_subclass__
UPDATERS: dict[str, type["Updater"]] = {}


@dataclass
class UpdateResult:
    """Result of an update check."""

    version: str
    hashes: dict[str, str]


class Updater(ABC):
    """Base class for source updaters. Subclasses auto-register via `name` attribute."""

    name: str  # Source name (e.g., "google-chrome")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name"):
            UPDATERS[cls.name] = cls

    @abstractmethod
    def fetch_version(self) -> str:
        """Fetch the latest version string."""

    @abstractmethod
    def fetch_hashes(self, version: str) -> dict[str, str]:
        """Fetch hashes for all platforms. Returns {nix_platform: sri_hash}."""

    def update(self, current: dict) -> UpdateResult | None:
        """Check for updates. Returns UpdateResult or None if up-to-date."""
        print(f"Fetching latest {self.name} version...")
        version = self.fetch_version()

        print(f"Latest version: {version}")
        if current.get("version") == version:
            print("Already at latest version")
            return None

        print("Fetching hashes for all platforms...")
        hashes = self.fetch_hashes(version)
        return UpdateResult(version=version, hashes=hashes)


# =============================================================================
# Source Updaters
# =============================================================================


class GoogleChromeUpdater(Updater):
    """Update Google Chrome to latest stable version."""

    name = "google-chrome"

    # nix platform -> download url
    PLATFORMS = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    def fetch_version(self) -> str:
        url = "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1"
        data = fetch_json(url)
        return data[0]["version"]

    def fetch_hashes(self, version: str) -> dict[str, str]:
        hashes = {}
        seen_urls: dict[str, str] = {}  # Cache for duplicate URLs (darwin universal)

        for platform, url in self.PLATFORMS.items():
            if url in seen_urls:
                hashes[platform] = seen_urls[url]
                print(f"  {platform}: (same as above)")
            else:
                sri = compute_sri_hash(url)
                hashes[platform] = sri
                seen_urls[url] = sri
                print(f"  {platform}: {sri[:32]}...")

        return hashes


class DataGripUpdater(Updater):
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

    def _fetch_release_info(self) -> dict:
        data = fetch_json(self.API_URL)
        return data["DG"][0]

    def fetch_version(self) -> str:
        return self._fetch_release_info()["version"]

    def fetch_hashes(self, version: str) -> dict[str, str]:
        release = self._fetch_release_info()
        hashes = {}

        for nix_platform, jb_key in self.PLATFORMS.items():
            checksum_url = release["downloads"][jb_key]["checksumLink"]
            with urllib.request.urlopen(checksum_url) as response:
                # Format: "hexhash *filename"
                hex_hash = response.read().decode().split()[0]
            sri = hex_to_sri(hex_hash)
            hashes[nix_platform] = sri
            print(f"  {nix_platform}: {sri[:32]}...")

        return hashes


class VSCodeInsidersUpdater(Updater):
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

    def fetch_version(self) -> str:
        first_platform = next(iter(self.PLATFORMS.values()))
        info = self._fetch_platform_info(first_platform)
        return info["productVersion"]

    def fetch_hashes(self, version: str) -> dict[str, str]:
        hashes = {}
        for platform, api_platform in self.PLATFORMS.items():
            info = self._fetch_platform_info(api_platform)
            sri = hex_to_sri(info["sha256hash"])
            hashes[platform] = sri
            print(f"  {platform}: {sri[:32]}...")
        return hashes


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
        sources[name] = {"version": result.version, "hashes": result.hashes}
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
