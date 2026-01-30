"""Base classes for source updaters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from filelock import FileLock

from lib.config import FAKE_HASH, get_config, get_current_nix_platform
from lib.events import EventCollector, EventKind, EventStream, UpdateEvent
from lib.models import (
    DrvType,
    HashCollection,
    HashEntry,
    HashType,
    PlatformMapping,
    SourceEntry,
    SourceHashes,
    SourcesFile,
    VersionInfo,
)
from lib.nix import (
    FlakeLock,
    compute_fixed_output_hash,
    compute_url_hashes,
)

if TYPE_CHECKING:
    import aiohttp


# =============================================================================
# Updater Registry
# =============================================================================

# Registry populated automatically via __init_subclass__
UPDATERS: dict[str, type["Updater"]] = {}


# =============================================================================
# Base Updater Class
# =============================================================================


class Updater(ABC):
    """Base class for source updaters.

    Subclasses auto-register via `name` class attribute.
    """

    name: str  # Source name (e.g., "google-chrome")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name") and cls.name:
            UPDATERS[cls.name] = cls

    @abstractmethod
    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        """Fetch the latest version and any metadata needed for hashes."""

    @abstractmethod
    def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        """Fetch hashes for the source."""

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build SourceEntry from version info and hashes. Override to customize."""
        return SourceEntry(
            version=info.version, hashes=HashCollection.from_value(hashes)
        )

    def _build_result_with_urls(
        self,
        info: VersionInfo,
        hashes: SourceHashes,
        urls: dict[str, str],
        *,
        commit: str | None = None,
    ) -> SourceEntry:
        """Helper for updaters that include download URLs in the result."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            urls=urls,
            commit=commit,
        )

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        """Check if current entry matches latest version info."""
        if current is None:
            return False
        if current.version != info.version:
            return False
        # For sources with commit tracking, also compare commits
        upstream_commit = info.metadata.get("commit")
        if upstream_commit and current.commit:
            return current.commit == upstream_commit
        return True

    async def update_stream(
        self, current: SourceEntry | None, session: "aiohttp.ClientSession"
    ) -> EventStream:
        """Check for updates. Yields UpdateEvent stream and final result."""
        yield UpdateEvent.status(self.name, f"Fetching latest {self.name} version...")
        info = await self.fetch_latest(session)

        yield UpdateEvent.status(self.name, f"Latest version: {info.version}")
        if self._is_latest(current, info):
            yield UpdateEvent.status(self.name, "Already at latest version")
            yield UpdateEvent.result(self.name)
            return

        yield UpdateEvent.status(self.name, "Fetching hashes for all platforms...")
        collector: EventCollector[SourceHashes] = EventCollector()
        async for event in collector.collect(self.fetch_hashes(info, session)):
            yield event
        hashes = collector.require_value("Missing hash output")
        result = self.build_result(info, hashes)
        if current is not None and result == current:
            yield UpdateEvent.status(self.name, "No updates needed")
            yield UpdateEvent.result(self.name)
            return
        yield UpdateEvent.result(self.name, result)


# =============================================================================
# Specialized Updater Base Classes
# =============================================================================


class ChecksumProvidedUpdater(Updater):
    """Base for sources that provide checksums in their API (no download needed)."""

    platforms: PlatformMapping

    @abstractmethod
    async def fetch_checksums(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> dict[str, str]:
        """Return {nix_platform: hex_hash} from API metadata."""

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        """Convert API checksums to SRI format."""
        from lib.nix import convert_hash_to_sri

        hashes: dict[str, str] = {}
        checksums = await self.fetch_checksums(info, session)
        for platform, hex_hash in checksums.items():
            collector: EventCollector[str] = EventCollector()
            async for event in collector.collect(
                convert_hash_to_sri(self.name, hex_hash)
            ):
                yield event
            hashes[platform] = collector.require_value("Missing checksum conversion")
        yield UpdateEvent.value(self.name, hashes)


class DownloadHashUpdater(Updater):
    """Base for sources requiring download to compute hash."""

    platforms: PlatformMapping

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform. Override for custom URL building."""
        return self.platforms.get_url(platform)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build result with platform URLs."""
        urls = {p: self.get_download_url(p, info) for p in self.platforms.platforms}
        return self._build_result_with_urls(info, hashes, urls)

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        """Compute hashes by downloading, deduplicating identical URLs."""
        platform_urls = {
            platform: self.get_download_url(platform, info)
            for platform in self.platforms.platforms
        }
        collector: EventCollector[dict[str, str]] = EventCollector()
        async for event in collector.collect(
            compute_url_hashes(self.name, platform_urls.values())
        ):
            yield event
        hashes_by_url = collector.require_value("Missing hash output")

        hashes: dict[str, str] = {
            platform: hashes_by_url[platform_urls[platform]]
            for platform in self.platforms.platforms
        }
        yield UpdateEvent.value(self.name, hashes)


class HashEntryUpdater(Updater):
    """Base for sources that emit hash entries in sources.json."""

    input_name: str | None = None

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            hashes=HashCollection.from_value(hashes), input=self.input_name
        )

    async def _emit_single_hash_entry(
        self,
        events: EventStream,
        *,
        error: str,
        drv_type: DrvType,
        hash_type: HashType,
    ) -> EventStream:
        collector: EventCollector[str] = EventCollector()
        async for event in collector.collect(events):
            yield event
        hash_value = collector.require_value(error)
        yield UpdateEvent.value(
            self.name, [HashEntry.create(drv_type, hash_type, hash_value)]
        )


class FlakeInputHashUpdater(HashEntryUpdater):
    """Base for hashes derived from flake inputs."""

    input_name: str | None = None
    drv_type: DrvType
    hash_type: HashType

    def __init__(self):
        if self.input_name is None:
            self.input_name = self.name

    @property
    def _input(self) -> str:
        """Return input_name, guaranteed non-None after __init__."""
        assert self.input_name is not None
        return self.input_name

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        lock = FlakeLock.load()
        version = lock.get_input_version(self._input)
        node = lock.get_input_node(self._input)
        return VersionInfo(version=version, metadata={"node": node})

    @abstractmethod
    def _compute_hash(self, info: VersionInfo) -> EventStream:
        """Return async iterator that yields hash computation events."""

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        async for event in self._emit_single_hash_entry(
            self._compute_hash(info),
            error=f"Missing {self.hash_type} output",
            drv_type=self.drv_type,
            hash_type=self.hash_type,
        ):
            yield event


# =============================================================================
# Specialized Flake Input Updaters
# =============================================================================


def _build_nixpkgs_expr(body: str) -> str:
    """Build a nix expression with nixpkgs prelude."""
    lock = FlakeLock.load()
    return f"""
      let
        pkgs = {lock.nixpkgs_expr()};
      in
        {body}
    """


async def _compute_nixpkgs_hash(source: str, expr_body: str) -> EventStream:
    """Compute hash for a nixpkgs expression that uses lib.fakeHash."""
    expr = _build_nixpkgs_expr(expr_body)
    async for event in compute_fixed_output_hash(source, expr):
        yield event


async def compute_go_vendor_hash(
    source: str,
    input_name: str,
    *,
    pname: str,
    version: str,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
) -> EventStream:
    """Compute Go vendor hash for a flake input."""
    lock = FlakeLock.load()
    subpackages_expr = ""
    if subpackages:
        quoted = " ".join(f'"{subpkg}"' for subpkg in subpackages)
        subpackages_expr = f"subPackages = [ {quoted} ];"
    proxy_expr = "proxyVendor = true;" if proxy_vendor else ""
    src_expr = lock.build_fetch_expr(input_name)
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.buildGoModule {{
        pname = "{pname}";
        version = "{version}";
        src = {src_expr};
        {subpackages_expr}
        {proxy_expr}
        vendorHash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


async def compute_cargo_vendor_hash(
    source: str, input_name: str, *, subdir: str | None = None
) -> EventStream:
    """Compute Cargo vendor hash for a flake input."""
    lock = FlakeLock.load()
    src_expr = lock.build_fetch_expr(input_name)
    if subdir:
        src_expr = f'"${{{src_expr}}}/{subdir}"'
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.rustPlatform.fetchCargoVendor {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


async def compute_npm_deps_hash(source: str, input_name: str) -> EventStream:
    """Compute npm deps hash for a flake input."""
    lock = FlakeLock.load()
    src_expr = lock.build_fetch_expr(input_name)
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.fetchNpmDeps {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


# Platforms for Deno deps (matches flake's systems list)
DENO_DEPS_PLATFORMS = ["aarch64-darwin", "aarch64-linux", "x86_64-linux"]


async def _compute_deno_deps_hash_for_platform(
    source: str, input_name: str, platform: str
) -> EventStream:
    """Compute Deno deps hash for a specific platform."""
    from lib.config import SRI_PREFIX
    from lib.nix import convert_hash_to_sri

    config = get_config()
    nix_attr = f'"{source}"'
    lock = FlakeLock.load()

    collector: EventCollector[str] = EventCollector()
    from lib.nix import run_command

    async for event in collector.collect(
        run_command(
            [
                "nix",
                "build",
                "-L",
                "--no-link",
                "--impure",
                "--expr",
                f"""
                  let
                    flake = builtins.getFlake "git+file://{config.paths.root}?dirty=1";
                    pkgs = import ({lock.build_fetch_expr(lock.get_root_input_name("nixpkgs"))}) {{
                      system = "{platform}";
                      overlays = [ flake.overlays.default ];
                    }};
                  in pkgs.{nix_attr}
                """,
            ],
            source=f"{source}:{platform}",
            error_message="nix build did not return output",
        )
    ):
        yield event

    from lib.nix import extract_nix_hash
    from lib.events import CommandResult

    result = collector.require_value("nix build did not return output")
    if not isinstance(result, CommandResult):
        raise TypeError(f"Expected CommandResult, got {type(result)}")

    if result.returncode == 0:
        raise RuntimeError(
            f"Expected nix build to fail with hash mismatch for {platform}, but it succeeded"
        )

    hash_value = extract_nix_hash(result.stderr + result.stdout)
    if not hash_value.startswith(SRI_PREFIX):
        sri_collector: EventCollector[str] = EventCollector()
        async for event in sri_collector.collect(
            convert_hash_to_sri(source, hash_value)
        ):
            yield event
        hash_value = sri_collector.require_value("Hash conversion failed")

    yield UpdateEvent.value(source, (platform, hash_value))


async def compute_deno_deps_hash(source: str, input_name: str) -> EventStream:
    """Compute Deno deps hash for all supported platforms."""
    config = get_config()
    current_platform = get_current_nix_platform()
    if current_platform not in DENO_DEPS_PLATFORMS:
        raise RuntimeError(
            f"Current platform {current_platform} not in supported platforms: "
            f"{DENO_DEPS_PLATFORMS}"
        )

    lock_path = config.paths.sources_file.with_suffix(".json.lock")
    with FileLock(lock_path):
        sources = SourcesFile.load()
        original_entry = sources.entries.get(source)

        # Preserve existing hashes for platforms we won't compute
        existing_hashes: dict[str, str] = {}
        if original_entry and original_entry.hashes.entries:
            for entry in original_entry.hashes.entries:
                if entry.platform:
                    existing_hashes[entry.platform] = entry.hash

        # Determine which platforms to compute
        platforms_to_compute = (
            [current_platform] if config.native_only else DENO_DEPS_PLATFORMS
        )

        try:
            platform_hashes: dict[str, str] = {}

            for platform in platforms_to_compute:
                yield UpdateEvent.status(source, f"Computing hash for {platform}...")

                # Set fake hash for platform being computed
                temp_entries = [
                    HashEntry.create(
                        "denoDeps",
                        "denoDepsHash",
                        (platform_hashes.get(p) or existing_hashes.get(p, FAKE_HASH)),
                        platform=p,
                    )
                    for p in DENO_DEPS_PLATFORMS
                ]
                temp_entry = SourceEntry(
                    hashes=HashCollection.from_value(temp_entries),
                    input=input_name,
                )
                sources.entries[source] = temp_entry
                sources.save()

                async for event in _compute_deno_deps_hash_for_platform(
                    source, input_name, platform
                ):
                    if event.kind == EventKind.VALUE and event.payload is not None:
                        plat, hash_val = event.payload
                        platform_hashes[plat] = hash_val
                    else:
                        yield event

            # Merge computed hashes with preserved existing hashes
            final_hashes = {**existing_hashes, **platform_hashes}
            yield UpdateEvent.value(source, final_hashes)
        finally:
            if original_entry is not None:
                sources.entries[source] = original_entry
            elif source in sources.entries:
                del sources.entries[source]
            sources.save()


class GoVendorHashUpdater(FlakeInputHashUpdater):
    """Updater for Go modules with vendor hash."""

    drv_type: DrvType = "buildGoModule"
    hash_type: HashType = "vendorHash"
    pname: str | None = None
    subpackages: list[str] | None = None
    proxy_vendor: bool = False

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_go_vendor_hash(
            self.name,
            self._input,
            pname=self.pname or self.name,
            version=info.version,
            subpackages=self.subpackages,
            proxy_vendor=self.proxy_vendor,
        )


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    """Updater for Rust crates with cargo vendor hash."""

    drv_type: DrvType = "fetchCargoVendor"
    hash_type: HashType = "cargoHash"
    subdir: str | None = None

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_cargo_vendor_hash(self.name, self._input, subdir=self.subdir)


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    """Updater for npm dependencies hash."""

    drv_type: DrvType = "fetchNpmDeps"
    hash_type: HashType = "npmDepsHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_npm_deps_hash(self.name, self._input)


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Updater for Deno dependencies hash (platform-specific)."""

    drv_type: DrvType = "denoDeps"
    hash_type: HashType = "denoDepsHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_deno_deps_hash(self.name, self._input)

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        """Override to handle multi-platform hash results."""
        collector: EventCollector[dict[str, str]] = EventCollector()
        async for event in collector.collect(self._compute_hash(info)):
            yield event

        platform_hashes = collector.require_value(f"Missing {self.hash_type} output")
        if not isinstance(platform_hashes, dict):
            raise TypeError(
                f"Expected dict of platform hashes, got {type(platform_hashes)}"
            )

        entries = [
            HashEntry.create(self.drv_type, self.hash_type, hash_val, platform=platform)
            for platform, hash_val in sorted(platform_hashes.items())
        ]
        yield UpdateEvent.value(self.name, entries)


# =============================================================================
# GitHub Raw File Updater
# =============================================================================


class GitHubRawFileUpdater(HashEntryUpdater):
    """Fetch latest raw file from GitHub and compute hash."""

    owner: str
    repo: str
    path: str

    async def fetch_latest(self, session: "aiohttp.ClientSession") -> VersionInfo:
        from lib.http import fetch_github_default_branch, fetch_github_latest_commit

        branch = await fetch_github_default_branch(session, self.owner, self.repo)
        rev = await fetch_github_latest_commit(
            session, self.owner, self.repo, self.path, branch
        )
        return VersionInfo(version=rev, metadata={"rev": rev, "branch": branch})

    async def fetch_hashes(
        self, info: VersionInfo, session: "aiohttp.ClientSession"
    ) -> EventStream:
        from lib.http import github_raw_url

        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        collector: EventCollector[dict[str, str]] = EventCollector()
        async for event in collector.collect(compute_url_hashes(self.name, [url])):
            yield event
        hashes_by_url = collector.require_value("Missing hash output")
        hash_value = hashes_by_url[url]
        yield UpdateEvent.value(
            self.name, [HashEntry.create("fetchurl", "sha256", hash_value, url=url)]
        )


# =============================================================================
# Factory Functions
# =============================================================================


def go_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    pname: str | None = None,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
) -> type[GoVendorHashUpdater]:
    """Create and register a Go vendor hash updater."""
    attrs = {
        "name": name,
        "input_name": input_name,
        "pname": pname,
        "subpackages": subpackages,
        "proxy_vendor": proxy_vendor,
    }
    return type(f"{name}Updater", (GoVendorHashUpdater,), attrs)


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    subdir: str | None = None,
) -> type[CargoVendorHashUpdater]:
    """Create and register a Cargo vendor hash updater."""
    attrs = {"name": name, "input_name": input_name, "subdir": subdir}
    return type(f"{name}Updater", (CargoVendorHashUpdater,), attrs)


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[NpmDepsHashUpdater]:
    """Create and register an npm deps hash updater."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (NpmDepsHashUpdater,), attrs)


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create and register a Deno deps hash updater."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (DenoDepsHashUpdater,), attrs)


def github_raw_file_updater(
    name: str,
    *,
    owner: str,
    repo: str,
    path: str,
) -> type[GitHubRawFileUpdater]:
    """Create and register a GitHub raw file updater."""
    attrs = {"name": name, "owner": owner, "repo": repo, "path": path}
    return type(f"{name}Updater", (GitHubRawFileUpdater,), attrs)
