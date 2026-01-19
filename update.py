#!/usr/bin/env python3
"""Update source versions and hashes in sources.json.

Usage:
    ./update.py <source>                 Update a specific source
    ./update.py <source> --update-input  Update and refresh flake input
    ./update.py --all                    Update all sources
    ./update.py --list                   List available sources

Sources are defined with custom update logic for fetching latest versions
and computing hashes from upstream release channels.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


# =============================================================================
# Configuration
# =============================================================================


def get_repo_file(filename: str) -> Path:
    """Resolve repo file location (handles nix store paths)."""
    script_path = Path(__file__)
    base_dir = Path.cwd() if "/nix/store" in str(script_path) else script_path.parent
    return base_dir / filename


SOURCES_FILE = get_repo_file("sources.json")
FLAKE_LOCK_FILE = get_repo_file("flake.lock")


# =============================================================================
# Utilities
# =============================================================================


DEFAULT_TIMEOUT = 30


def _resolve_hash_workers(total: int) -> int:
    try:
        configured = int(os.environ.get("UPDATE_HASH_WORKERS", "4"))
    except ValueError:
        configured = 4
    return max(1, min(configured, total))


def _resolve_timeout(timeout: float | None) -> float:
    return DEFAULT_TIMEOUT if timeout is None else timeout


def _open_url(
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    method: str = "GET",
) -> Any:
    req = urllib.request.Request(url, method=method)
    if user_agent:
        req.add_header("User-Agent", user_agent)
    try:
        return urllib.request.urlopen(req, timeout=_resolve_timeout(timeout))
    except urllib.error.HTTPError as err:
        error_body = err.read().decode(errors="ignore").strip()
        detail = f"HTTP {err.code} {err.reason}"
        if error_body:
            detail = f"{detail}\n{error_body}"
        raise RuntimeError(f"Request to {url} failed: {detail}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Request to {url} failed: {err.reason}") from err


def fetch_url(
    url: str, *, user_agent: str | None = None, timeout: float | None = None
) -> bytes:
    """Fetch content from a URL with optional user agent."""
    with _open_url(url, user_agent=user_agent, timeout=timeout) as response:
        return response.read()


def fetch_url_with_headers(
    url: str, *, user_agent: str | None = None, timeout: float | None = None
) -> tuple[bytes, Mapping[str, str]]:
    with _open_url(url, user_agent=user_agent, timeout=timeout) as response:
        return response.read(), response.headers


def _check_github_rate_limit(headers: Mapping[str, str], url: str) -> None:
    remaining = headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    try:
        remaining_value = int(remaining)
    except ValueError:
        return
    if remaining_value > 0:
        return
    reset = headers.get("X-RateLimit-Reset")
    reset_time = "unknown"
    if reset and reset.isdigit():
        reset_time = datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
    raise RuntimeError(
        f"GitHub API rate limit exceeded for {url}. Resets at {reset_time}."
    )


def fetch_json(
    url: str, *, user_agent: str | None = None, timeout: float | None = None
) -> dict:
    """Fetch and parse JSON from a URL."""
    if url.startswith("https://api.github.com/"):
        payload, headers = fetch_url_with_headers(
            url, user_agent=user_agent, timeout=timeout
        )
        _check_github_rate_limit(headers, url)
    else:
        payload = fetch_url(url, user_agent=user_agent, timeout=timeout)
    try:
        return json.loads(payload.decode())
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON response from {url}: {err}") from err


def _format_command_output(result: subprocess.CompletedProcess[str]) -> str:
    chunks = []
    if result.stdout:
        chunks.append(f"stdout:\n{result.stdout.strip()}")
    if result.stderr:
        chunks.append(f"stderr:\n{result.stderr.strip()}")
    return "\n".join(chunks) if chunks else "(no output)"


def run_command(
    args: list[str],
    *,
    purpose: str,
    check: bool = True,
    print_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"{purpose} failed (exit {result.returncode}).\n{_format_command_output(result)}"
        )
    if print_output:
        if result.stdout:
            print(result.stdout.strip())
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
    return result


def _nix_hash_to_sri(hash_value: str) -> str:
    """Convert any nix hash format (base32, hex) to SRI format."""
    result = run_command(
        ["nix", "hash", "convert", "--hash-algo", "sha256", "--to", "sri", hash_value],
        purpose="nix hash convert",
    )
    return result.stdout.strip()


def compute_sri_hash(url: str) -> str:
    """Compute SRI hash for a URL using nix-prefetch-url."""
    result = run_command(
        ["nix-prefetch-url", "--type", "sha256", url],
        purpose=f"nix-prefetch-url for {url}",
    )
    base32_hash = result.stdout.strip().split("\n")[-1]
    return _nix_hash_to_sri(base32_hash)


def hex_to_sri(hex_hash: str) -> str:
    """Convert hex sha256 hash to SRI format."""
    return _nix_hash_to_sri(hex_hash)


def load_flake_lock() -> dict:
    """Load flake.lock nodes."""
    if not FLAKE_LOCK_FILE.exists():
        raise FileNotFoundError(f"flake.lock not found at {FLAKE_LOCK_FILE}")
    return json.loads(FLAKE_LOCK_FILE.read_text())["nodes"]


def get_flake_input_node(input_name: str) -> dict:
    """Return flake.lock node for an input."""
    lock = load_flake_lock()
    if input_name not in lock:
        raise KeyError(f"flake input '{input_name}' not found in flake.lock")
    return lock[input_name]


def get_root_input_name(input_name: str) -> str:
    """Return the node name for a root input."""
    lock = load_flake_lock()
    root_inputs = lock.get("root", {}).get("inputs", {})
    return root_inputs.get(input_name, input_name)


def get_flake_input_version(node: dict) -> str:
    """Best-effort version string for a flake input."""
    original = node.get("original", {})
    return (
        original.get("ref")
        or original.get("rev")
        or node.get("locked", {}).get("rev")
        or "unknown"
    )


def flake_fetch_expr(node: dict) -> str:
    """Build a nix expression to fetch a flake input."""
    locked = node.get("locked", {})
    if locked.get("type") not in {"github", "gitlab"}:
        raise ValueError(f"Unsupported flake input type: {locked.get('type')}")
    return (
        "builtins.fetchTree { "
        f'type = "{locked["type"]}"; '
        f'owner = "{locked["owner"]}"; '
        f'repo = "{locked["repo"]}"; '
        f'rev = "{locked["rev"]}"; '
        f'narHash = "{locked["narHash"]}"; '
        "}"
    )


def nixpkgs_expr() -> str:
    node_name = get_root_input_name("nixpkgs")
    node = get_flake_input_node(node_name)
    return f"import ({flake_fetch_expr(node)}) {{ system = builtins.currentSystem; }}"


def update_flake_input(input_name: str) -> None:
    """Update a flake input in flake.lock."""
    run_command(
        ["nix", "flake", "lock", "--update-input", input_name],
        purpose=f"nix flake lock --update-input {input_name}",
        print_output=True,
    )


def _extract_nix_hash(output: str) -> str:
    sri_match = re.search(r"got:\s*(sha256-[0-9A-Za-z+/=]+)", output)
    if sri_match:
        return sri_match.group(1)
    fallback_match = re.search(
        r"got:\s*(sha256:[0-9a-fA-F]{64}|[0-9a-fA-F]{64}|[0-9a-z]{52})",
        output,
    )
    if fallback_match:
        return _nix_hash_to_sri(fallback_match.group(1))
    raise RuntimeError(f"Could not find hash in nix output:\n{output.strip()}")


def compute_fixed_output_hash(expr: str) -> str:
    """Compute hash by running a nix expression with lib.fakeHash."""
    result = run_command(
        ["nix", "build", "--no-link", "--impure", "--expr", expr],
        purpose="nix build",
        check=False,
    )
    if result.returncode == 0:
        raise RuntimeError(
            "Expected nix build to fail with hash mismatch, but it succeeded"
        )
    return _extract_nix_hash(result.stderr + result.stdout)


def _flake_expr_prelude() -> str:
    return f"""
      let
        pkgs = {nixpkgs_expr()};
      in
    """


def compute_go_vendor_hash(
    input_name: str,
    *,
    pname: str,
    version: str,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
) -> str:
    subpackages_expr = ""
    if subpackages:
        quoted = " ".join(f'"{sp}"' for sp in subpackages)
        subpackages_expr = f"subPackages = [ {quoted} ];"
    proxy_expr = "proxyVendor = true;" if proxy_vendor else ""
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    expr = f"""
    {_flake_expr_prelude()}
      pkgs.buildGoModule {{
        pname = \"{pname}\";
        version = \"{version}\";
        src = {src_expr};
        {subpackages_expr}
        {proxy_expr}
        vendorHash = pkgs.lib.fakeHash;
      }}
    """
    return compute_fixed_output_hash(expr)


def compute_cargo_vendor_hash(input_name: str, *, subdir: str | None = None) -> str:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    if subdir:
        src_expr = f'"${{{src_expr}}}/{subdir}"'
    expr = f"""
    {_flake_expr_prelude()}
      pkgs.rustPlatform.fetchCargoVendor {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}
    """
    return compute_fixed_output_hash(expr)


def compute_npm_deps_hash(input_name: str) -> str:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    expr = f"""
    {_flake_expr_prelude()}
      pkgs.fetchNpmDeps {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}
    """
    return compute_fixed_output_hash(expr)


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


def fetch_github_default_branch(owner: str, repo: str) -> str:
    data = fetch_json(
        f"https://api.github.com/repos/{owner}/{repo}",
        user_agent="update.py",
        timeout=DEFAULT_TIMEOUT,
    )
    return data["default_branch"]


def fetch_github_latest_commit(owner: str, repo: str, path: str, branch: str) -> str:
    encoded_path = urllib.parse.quote(path)
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?path={encoded_path}&sha={branch}&per_page=1"
    data = fetch_json(url, user_agent="update.py", timeout=DEFAULT_TIMEOUT)
    if not data:
        raise RuntimeError(f"No commits found for {owner}/{repo}:{path}")
    return data[0]["sha"]


def make_hash_entry(
    drv_type: str,
    hash_type: str,
    hash_value: str,
    *,
    url: str | None = None,
    urls: dict[str, str] | None = None,
) -> HashEntry:
    return HashEntry(
        drv_type=drv_type,
        hash_type=hash_type,
        hash=hash_value,
        url=url,
        urls=urls,
    )


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


@dataclass(frozen=True)
class HashEntry:
    """Single hash entry for sources.json."""

    drv_type: str
    hash_type: str
    hash: str
    url: str | None = None
    urls: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HashEntry":
        return cls(
            drv_type=str(data["drvType"]),
            hash_type=str(data["hashType"]),
            hash=str(data["hash"]),
            url=data.get("url"),
            urls=data.get("urls"),
        )

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "drvType": self.drv_type,
            "hashType": self.hash_type,
            "hash": self.hash,
        }
        if self.url is not None:
            entry["url"] = self.url
        if self.urls is not None:
            entry["urls"] = self.urls
        return entry


SourceHashes = dict[str, str] | list[HashEntry]


@dataclass(frozen=True)
class SourceEntry:
    """Normalized schema for sources.json entries."""

    hashes: SourceHashes
    version: str | None = None
    input: str | None = None
    urls: dict[str, str] | None = None
    commit: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceEntry":
        if "hashes" not in data:
            raise ValueError("Source entry is missing 'hashes'")
        hashes_value = data["hashes"]
        if isinstance(hashes_value, list):
            hashes = [HashEntry.from_dict(item) for item in hashes_value]
        elif isinstance(hashes_value, dict):
            hashes = dict(hashes_value)
        else:
            raise TypeError("Source entry 'hashes' must be a list or dict")
        return cls(
            hashes=hashes,
            version=data.get("version"),
            input=data.get("input"),
            urls=data.get("urls"),
            commit=data.get("commit"),
        )

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "hashes": (
                [hash_entry.to_dict() for hash_entry in self.hashes]
                if isinstance(self.hashes, list)
                else self.hashes
            )
        }
        if self.version is not None:
            entry["version"] = self.version
        if self.input is not None:
            entry["input"] = self.input
        if self.urls is not None:
            entry["urls"] = self.urls
        if self.commit is not None:
            entry["commit"] = self.commit
        return entry


@dataclass
class SourcesFile:
    """Container for sources.json entries."""

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourcesFile":
        return cls(
            entries={
                str(name): SourceEntry.from_dict(entry) for name, entry in data.items()
            }
        )

    @classmethod
    def load(cls, path: Path) -> "SourcesFile":
        if not path.exists():
            return cls(entries={})
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")


@dataclass
class VersionInfo:
    """Version and metadata fetched from upstream."""

    version: str
    metadata: dict[
        str, Any
    ]  # Updater-specific data (URLs, checksums, release info, etc.)


@dataclass
class UpdateResult:
    """Result of an update check."""

    entry: SourceEntry


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
    def fetch_hashes(self, info: VersionInfo) -> SourceHashes:
        """Fetch hashes for the source."""

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        """Build UpdateResult from version info and hashes. Override to customize."""
        entry = SourceEntry(version=info.version, hashes=hashes)
        return UpdateResult(entry=entry)

    def update(self, current: SourceEntry | None) -> UpdateResult | None:
        """Check for updates. Returns UpdateResult or None if up-to-date."""
        print(f"Fetching latest {self.name} version...")
        info = self.fetch_latest()

        print(f"Latest version: {info.version}")
        current_version = current.version if current else None
        if current_version == info.version:
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
        hashes: dict[str, str] = {}
        platform_urls = {
            platform: self.get_download_url(platform, info)
            for platform in self.PLATFORMS
        }
        unique_urls: list[str] = []
        for url in platform_urls.values():
            if url not in unique_urls:
                unique_urls.append(url)

        hashes_by_url: dict[str, str] = {}
        max_workers = _resolve_hash_workers(len(unique_urls))
        if max_workers == 1 or len(unique_urls) == 1:
            for url in unique_urls:
                hashes_by_url[url] = compute_sri_hash(url)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(compute_sri_hash, url): url for url in unique_urls
                }
                for future in as_completed(future_map):
                    url = future_map[future]
                    hashes_by_url[url] = future.result()

        seen_urls: set[str] = set()
        for platform in self.PLATFORMS:
            url = platform_urls[platform]
            sri = hashes_by_url[url]
            hashes[platform] = sri
            if url in seen_urls:
                _print_hash(platform, sri, "(same as above)")
            else:
                _print_hash(platform, sri)
                seen_urls.add(url)

        return hashes


class HashEntryUpdater(Updater):
    """Base for sources that emit hash entries in sources.json."""

    input_name: str | None = None

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        entry = SourceEntry(hashes=hashes, input=self.input_name)
        return UpdateResult(entry=entry)


class FlakeInputHashUpdater(HashEntryUpdater):
    """Base for hashes derived from flake inputs."""

    input_name: str | None = None

    def __init__(self):
        if self.input_name is None:
            self.input_name = self.name

    def fetch_latest(self) -> VersionInfo:
        input_name = self.input_name or self.name
        node = get_flake_input_node(input_name)
        version = get_flake_input_version(node)
        return VersionInfo(version=version, metadata={"node": node})


class GoVendorHashUpdater(FlakeInputHashUpdater):
    drv_type = "buildGoModule"
    hash_type = "vendorHash"
    pname: str | None = None
    subpackages: list[str] | None = None
    proxy_vendor: bool = False

    def fetch_hashes(self, info: VersionInfo) -> list[HashEntry]:
        input_name = self.input_name or self.name
        hash_value = compute_go_vendor_hash(
            input_name,
            pname=self.pname or self.name,
            version=info.version,
            subpackages=self.subpackages,
            proxy_vendor=self.proxy_vendor,
        )
        return [make_hash_entry(self.drv_type, self.hash_type, hash_value)]


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchCargoVendor"
    hash_type = "cargoHash"
    subdir: str | None = None

    def fetch_hashes(self, info: VersionInfo) -> list[HashEntry]:
        input_name = self.input_name or self.name
        hash_value = compute_cargo_vendor_hash(input_name, subdir=self.subdir)
        return [make_hash_entry(self.drv_type, self.hash_type, hash_value)]


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchNpmDeps"
    hash_type = "npmDepsHash"

    def fetch_hashes(self, info: VersionInfo) -> list[HashEntry]:
        input_name = self.input_name or self.name
        hash_value = compute_npm_deps_hash(input_name)
        return [make_hash_entry(self.drv_type, self.hash_type, hash_value)]


# =============================================================================
# Source Updaters
# =============================================================================


class GitHubRawFileUpdater(HashEntryUpdater):
    """Fetch latest raw file from GitHub and compute hash."""

    owner: str
    repo: str
    path: str

    def fetch_latest(self) -> VersionInfo:
        branch = fetch_github_default_branch(self.owner, self.repo)
        rev = fetch_github_latest_commit(self.owner, self.repo, self.path, branch)
        return VersionInfo(version=rev, metadata={"rev": rev, "branch": branch})

    def fetch_hashes(self, info: VersionInfo) -> list[HashEntry]:
        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        hash_value = compute_sri_hash(url)
        return [make_hash_entry("fetchurl", "sha256", hash_value, url=url)]


class HomebrewZshCompletionUpdater(GitHubRawFileUpdater):
    name = "homebrew-zsh-completion"
    owner = "Homebrew"
    repo = "brew"
    path = "completions/zsh/_brew"


class GituiKeyConfigUpdater(GitHubRawFileUpdater):
    name = "gitui-key-config"
    owner = "extrawurst"
    repo = "gitui"
    path = "vim_style_key_config.ron"


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
            # Format: "hexhash *filename"
            hex_hash = (
                fetch_url(checksum_url, timeout=DEFAULT_TIMEOUT).decode().split()[0]
            )
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
        xml_data = fetch_url(
            self.APPCAST_URL, user_agent="Sparkle/2.0", timeout=DEFAULT_TIMEOUT
        ).decode()

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

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        """Include the versioned URL in the result."""
        entry = SourceEntry(
            version=info.version,
            hashes=hashes,
            urls={"darwin": info.metadata["url"]},
        )
        return UpdateResult(entry=entry)


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
        with _open_url(url, method="HEAD", timeout=DEFAULT_TIMEOUT) as response:
            # Get final URL after redirects
            final_url = response.geturl()

        # Fetch headers from the final URL
        with _open_url(final_url, method="HEAD", timeout=DEFAULT_TIMEOUT) as response:
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

        versions = {
            platform: info["productVersion"] for platform, info in platform_info.items()
        }
        unique_versions = set(versions.values())
        if len(unique_versions) != 1:
            raise RuntimeError(f"VS Code Insiders version mismatch: {versions}")
        version = unique_versions.pop()
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    def fetch_checksums(self, info: VersionInfo) -> dict[str, str]:
        platform_info = info.metadata["platform_info"]
        return {
            platform: platform_info[platform]["sha256hash"]
            for platform in self.PLATFORMS
        }


class AxiomCliUpdater(GoVendorHashUpdater):
    name = "axiom-cli"
    subpackages = ["cmd/axiom"]


class BeadsUpdater(GoVendorHashUpdater):
    name = "beads"
    subpackages = ["cmd/bd"]
    proxy_vendor = True


class CodexUpdater(CargoVendorHashUpdater):
    name = "codex"
    subdir = "codex-rs"


class CrushUpdater(GoVendorHashUpdater):
    name = "crush"


class GeminiCliUpdater(NpmDepsHashUpdater):
    name = "gemini-cli"


class SentryCliUpdater(CargoVendorHashUpdater):
    name = "sentry-cli"


# =============================================================================
# Main
# =============================================================================


def load_sources() -> SourcesFile:
    return SourcesFile.load(SOURCES_FILE)


def save_sources(sources: SourcesFile) -> None:
    sources.save(SOURCES_FILE)


def update_source(
    name: str,
    sources: SourcesFile,
    *,
    update_input: bool = False,
    raise_on_error: bool = False,
) -> bool:
    """Update a single source. Returns True if updated."""
    if name not in UPDATERS:
        message = f"Error: Unknown source '{name}'"
        if raise_on_error:
            raise ValueError(message)
        print(message)
        print(f"Available sources: {', '.join(UPDATERS.keys())}")
        return False

    print(f"\n{'=' * 60}")
    print(f"Updating {name}")
    print("=" * 60)

    current = sources.entries.get(name)
    updater = UPDATERS[name]()
    input_name = getattr(updater, "input_name", None)

    try:
        if update_input and input_name:
            print(f"Updating flake input '{input_name}'...")
            update_flake_input(input_name)

        result = updater.update(current)
        if result is None:
            return False
        entry = result.entry
        if current is not None and entry == current:
            print("No updates needed.")
            return False
        sources.entries[name] = entry
        return True
    except Exception as e:
        message = f"Error updating {name}: {e}"
        if raise_on_error:
            raise RuntimeError(message) from e
        print(message)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Update source versions and hashes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument("source", nargs="?", help="Source to update")
    parser.add_argument("-a", "--all", action="store_true", help="Update all sources")
    parser.add_argument(
        "-l", "--list", action="store_true", help="List available sources"
    )
    parser.add_argument(
        "--update-input",
        action="store_true",
        help="Update flake input(s) before hashing",
    )
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
            if update_source(name, sources, update_input=args.update_input):
                updated = True
    else:
        updated = update_source(
            args.source,
            sources,
            update_input=args.update_input,
            raise_on_error=True,
        )

    if updated:
        save_sources(sources)
        print(f"\nUpdated {SOURCES_FILE}")
        print("Run: nh darwin switch --no-nom .")
    else:
        print("\nNo updates needed.")


if __name__ == "__main__":
    main()
