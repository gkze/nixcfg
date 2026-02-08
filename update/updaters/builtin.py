from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import aiohttp

from libnix.models.sources import (
    HashCollection,
    HashEntry,
    HashMapping,
    SourceEntry,
    SourceHashes,
)
from libnix.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    _require_value,
    drain_value_events,
)
from update.flake import get_flake_input_node, get_flake_input_version
from update.net import (
    _request,
    fetch_github_api,
    fetch_github_default_branch,
    fetch_github_latest_commit,
    fetch_json,
    fetch_url,
    github_raw_url,
)
from update.nix import (
    _build_nix_expr,
    compute_fixed_output_hash,
    compute_import_cargo_lock_output_hashes,
)
from update.process import compute_url_hashes
from update.updaters.base import (
    CargoLockGitDep,
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    HashEntryUpdater,
    Updater,
    VersionInfo,
    _verify_platform_versions,
    bun_node_modules_updater,
    deno_deps_updater,
    go_vendor_updater,
    npm_deps_updater,
)

VSCODE_PLATFORMS = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-linux": "linux-x64",
}


def github_raw_file_updater(
    name: str,
    *,
    owner: str,
    repo: str,
    path: str,
) -> type[GitHubRawFileUpdater]:
    attrs = {"name": name, "owner": owner, "repo": repo, "path": path}
    return type(f"{name}Updater", (GitHubRawFileUpdater,), attrs)


class GitHubRawFileUpdater(HashEntryUpdater):
    owner: str
    repo: str
    path: str

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        branch = await fetch_github_default_branch(
            session, self.owner, self.repo, config=self.config
        )
        rev = await fetch_github_latest_commit(
            session, self.owner, self.repo, self.path, branch, config=self.config
        )
        return VersionInfo(version=rev, metadata={"rev": rev, "branch": branch})

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        hashes_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_url_hashes(self.name, [url]), hashes_drain
        ):
            yield event
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")
        hash_value = hashes_by_url[url]
        yield UpdateEvent.value(
            self.name, [HashEntry.create("sha256", hash_value, url=url)]
        )


github_raw_file_updater(
    "homebrew-zsh-completion",
    owner="Homebrew",
    repo="brew",
    path="completions/zsh/_brew",
)
github_raw_file_updater(
    "gitui-key-config",
    owner="extrawurst",
    repo="gitui",
    path="vim_style_key_config.ron",
)


class GoogleChromeUpdater(DownloadHashUpdater):
    name = "google-chrome"
    PLATFORMS = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = cast(
            "list[dict[str, str]]",
            await fetch_json(
                session,
                "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1",
                config=self.config,
            ),
        )
        if not data:
            msg = "No Chrome releases returned from chromiumdash"
            raise RuntimeError(msg)
        version = data[0].get("version")
        if not version:
            msg = f"Missing version in chromiumdash response: {data[0]}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata={})


class DataGripUpdater(ChecksumProvidedUpdater):
    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    PLATFORMS = {
        "aarch64-darwin": "macM1",
        "aarch64-linux": "linuxARM64",
        "x86_64-linux": "linux",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = cast(
            "dict[str, list[dict[str, str]]]",
            await fetch_json(session, self.API_URL, config=self.config),
        )
        releases = data.get("DG") or []
        if not releases:
            msg = f"No DataGrip releases found in response: {data}"
            raise RuntimeError(msg)
        release = releases[0]
        version = release.get("version")
        if not version:
            msg = f"Missing DataGrip version in release payload: {release}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata={"release": release})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        release = info.metadata["release"]

        async def _fetch_one(nix_platform: str, jetbrains_key: str) -> tuple[str, str]:
            checksum_url = release["downloads"][jetbrains_key]["checksumLink"]
            payload = await fetch_url(
                session,
                checksum_url,
                timeout=self.config.default_timeout,
                config=self.config,
            )
            parts = payload.decode().split()
            if not parts:
                msg = f"Empty checksum payload from {checksum_url}"
                raise RuntimeError(msg)
            return nix_platform, parts[0]

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items())
        )
        return dict(results)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        release = info.metadata["release"]
        urls = {
            nix_platform: release["downloads"][jetbrains_key]["link"]
            for nix_platform, jetbrains_key in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class ChatGPTUpdater(DownloadHashUpdater):
    name = "chatgpt"

    APPCAST_URL = (
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    )

    PLATFORMS = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        xml_payload = await fetch_url(
            session,
            self.APPCAST_URL,
            user_agent="Sparkle/2.0",
            timeout=self.config.default_timeout,
            config=self.config,
        )
        xml_data = xml_payload.decode()

        try:
            root = ET.fromstring(xml_data)  # noqa: S314 — trusted appcast XML
        except ET.ParseError as exc:
            snippet = xml_data[:200].replace("\n", " ").strip()
            msg = f"Invalid appcast XML from {self.APPCAST_URL}: {exc}; snippet: {snippet}"
            raise RuntimeError(msg) from exc
        item = root.find(".//item")
        if item is None:
            msg = "No items found in appcast"
            raise RuntimeError(msg)

        ns = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}

        version_elem = item.find("sparkle:shortVersionString", ns)
        if version_elem is None or version_elem.text is None:
            msg = "No version found in appcast"
            raise RuntimeError(msg)

        enclosure = item.find("enclosure")
        if enclosure is None:
            msg = "No enclosure found in appcast"
            raise RuntimeError(msg)

        url = enclosure.get("url")
        if url is None:
            msg = "No URL found in enclosure"
            raise RuntimeError(msg)

        return VersionInfo(version=version_elem.text, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["url"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return self._build_result_with_urls(
            info, hashes, {"darwin": info.metadata["url"]}
        )


class DroidUpdater(ChecksumProvidedUpdater):
    name = "droid"

    INSTALL_SCRIPT_URL = "https://app.factory.ai/cli"
    BASE_URL = "https://downloads.factory.ai/factory-cli/releases"

    _PLATFORM_INFO: dict[str, tuple[str, str]] = {
        "aarch64-darwin": ("darwin", "arm64"),
        "x86_64-darwin": ("darwin", "x64"),
        "aarch64-linux": ("linux", "arm64"),
        "x86_64-linux": ("linux", "x64"),
    }
    PLATFORMS = dict.fromkeys(_PLATFORM_INFO, "")

    def _download_url(self, nix_platform: str, version: str) -> str:
        os_name, arch = self._PLATFORM_INFO[nix_platform]
        return f"{self.BASE_URL}/{version}/{os_name}/{arch}/droid"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        script = await fetch_url(
            session,
            self.INSTALL_SCRIPT_URL,
            timeout=self.config.default_timeout,
            config=self.config,
        )
        match = re.search(r'VER="([^"]+)"', script.decode())
        if not match:
            msg = "Could not parse version from Factory CLI install script"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group(1), metadata={})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        async def _fetch_one(nix_platform: str) -> tuple[str, str]:
            sha_url = f"{self._download_url(nix_platform, info.version)}.sha256"
            payload = await fetch_url(
                session,
                sha_url,
                timeout=self.config.default_timeout,
                config=self.config,
            )
            checksum = payload.decode().strip()
            if not checksum:
                msg = f"Empty checksum payload from {sha_url}"
                raise RuntimeError(msg)
            return nix_platform, checksum

        results = await asyncio.gather(*(_fetch_one(p) for p in self._PLATFORM_INFO))
        return dict(results)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: self._download_url(p, info.version) for p in self._PLATFORM_INFO}
        return self._build_result_with_urls(info, hashes, urls)


class ConductorUpdater(DownloadHashUpdater):
    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    PLATFORMS = {"aarch64-darwin": "dmg-aarch64", "x86_64-darwin": "dmg-x86_64"}

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        url = f"{self.BASE_URL}/dmg-aarch64"
        _payload, headers = await _request(
            session,
            url,
            method="HEAD",
            timeout=self.config.default_timeout,
            config=self.config,
        )
        match = re.search(
            r"Conductor_([0-9.]+)_", headers.get("Content-Disposition", "")
        )
        if not match:
            msg = "Could not parse version from Content-Disposition"
            raise RuntimeError(msg)
        return VersionInfo(version=match.group(1), metadata={})


class SculptorUpdater(DownloadHashUpdater):
    name = "sculptor"
    BASE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor"
    PLATFORMS = {
        "aarch64-darwin": "Sculptor.dmg",
        "x86_64-darwin": "Sculptor-x86_64.dmg",
        "x86_64-linux": "AppImage/x64/Sculptor.AppImage",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        url = f"{self.BASE_URL}/Sculptor.dmg"
        _payload, headers = await _request(
            session,
            url,
            method="HEAD",
            timeout=self.config.default_timeout,
            config=self.config,
        )
        last_modified = headers.get("Last-Modified", "")
        if not last_modified:
            msg = "No Last-Modified header from Sculptor download"
            raise RuntimeError(msg)
        try:
            dt = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z").replace(
                tzinfo=UTC
            )
            version = dt.strftime("%Y-%m-%d")
        except ValueError:
            version = last_modified[:10]
        return VersionInfo(version=version, metadata={})


class PlatformAPIUpdater(ChecksumProvidedUpdater):
    VERSION_KEY: str = "version"
    CHECKSUM_KEY: str | None = None

    def _api_url(self, api_platform: str) -> str:
        raise NotImplementedError

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        raise NotImplementedError

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        async def _fetch_one(
            nix_plat: str, api_plat: str
        ) -> tuple[str, dict[str, str]]:
            data = await fetch_json(
                session, self._api_url(api_plat), config=self.config
            )
            return nix_plat, cast("dict[str, str]", data)

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items())
        )
        platform_info = dict(results)
        versions = {p: info[self.VERSION_KEY] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, self.name)
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        if not self.CHECKSUM_KEY:
            msg = "No CHECKSUM_KEY defined"
            raise NotImplementedError(msg)
        platform_info = info.metadata["platform_info"]
        return {p: platform_info[p][self.CHECKSUM_KEY] for p in self.PLATFORMS}

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class VSCodeInsidersUpdater(PlatformAPIUpdater):
    name = "vscode-insiders"
    PLATFORMS = VSCODE_PLATFORMS
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, api_platform: str) -> str:
        return f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        return f"https://update.code.visualstudio.com/{info.version}/{api_platform}/insider"


class OpencodeDesktopCargoLockUpdater(HashEntryUpdater):
    name = "opencode-desktop"
    input_name = "opencode"
    required_tools = ("nix",)
    package_attr = "desktop"
    lockfile_path = "packages/desktop/src-tauri/Cargo.lock"
    git_deps = [
        CargoLockGitDep("specta-2.0.0-rc.22", "spectaOutputHash", "specta"),
        CargoLockGitDep("tauri-2.9.5", "tauriOutputHash", "tauri"),
        CargoLockGitDep(
            "tauri-specta-2.0.0-rc.21", "tauriSpectaOutputHash", "tauri-specta"
        ),
    ]

    @property
    def _input(self) -> str:
        if self.input_name is None:
            raise RuntimeError("Missing input name")
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        locked_rev = node.locked.rev if node.locked else None
        return VersionInfo(
            version=version, metadata={"node": node, "commit": locked_rev}
        )

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        if current is None:
            return False
        upstream_rev = info.metadata.get("commit")
        if upstream_rev and current.commit:
            return current.commit == upstream_rev
        return False

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            hashes=HashCollection.from_value(hashes),
            input=self.input_name,
            commit=info.metadata.get("commit"),
        )

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        hash_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_import_cargo_lock_output_hashes(
                self.name,
                self._input,
                package_attr=self.package_attr,
                lockfile_path=self.lockfile_path,
                git_deps=self.git_deps,
                config=self.config,
            ),
            hash_drain,
        ):
            yield event
        hashes = _require_value(hash_drain, "Missing importCargoLock output hashes")
        entries = []
        for dep in self.git_deps:
            hash_value = hashes.get(dep.git_dep)
            if not hash_value:
                msg = f"Missing hash for {dep.git_dep}"
                raise RuntimeError(msg)
            entries.append(
                HashEntry.create(
                    dep.hash_type,
                    hash_value,
                    git_dep=dep.git_dep,
                )
            )
        yield UpdateEvent.value(self.name, entries)


class SentryCliUpdater(Updater):
    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = cast(
            "dict[str, str]",
            await fetch_github_api(
                session,
                f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
                config=self.config,
            ),
        )
        return VersionInfo(version=data["tag_name"], metadata={})

    def _src_nix_expr(self, version: str, hash_value: str = "pkgs.lib.fakeHash") -> str:
        return (
            f"pkgs.fetchFromGitHub {{\n"
            f'  owner = "{self.GITHUB_OWNER}";\n'
            f'  repo = "{self.GITHUB_REPO}";\n'
            f'  tag = "{version}";\n'
            f"  hash = {hash_value};\n"
            f'  postFetch = "{self.XCARCHIVE_FILTER}";\n'
            f"}}"
        )

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(self._src_nix_expr(info.version)),
            ),
            src_hash_drain,
        ):
            yield event
        src_hash = _require_value(src_hash_drain, "Missing srcHash output")

        cargo_hash_drain = ValueDrain[str]()
        src_expr = self._src_nix_expr(info.version, f'"{src_hash}"')
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(
                    f"pkgs.rustPlatform.fetchCargoVendor {{\n"
                    f"  src = {src_expr};\n"
                    f"  hash = pkgs.lib.fakeHash;\n"
                    f"}}"
                ),
            ),
            cargo_hash_drain,
        ):
            yield event
        cargo_hash = _require_value(cargo_hash_drain, "Missing cargoHash output")

        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("cargoHash", cargo_hash),
            ],
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
        )


class CodeCursorUpdater(DownloadHashUpdater):
    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        async def _fetch_one(
            nix_plat: str, api_plat: str
        ) -> tuple[str, dict[str, str]]:
            data = await fetch_json(
                session,
                f"{self.API_BASE}?platform={api_plat}&releaseTrack=stable",
                config=self.config,
            )
            return nix_plat, cast("dict[str, str]", data)

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items())
        )
        platform_info = dict(results)
        versions = {p: info["version"] for p, info in platform_info.items()}
        commits = {p: info["commitSha"] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, "Cursor")
        commit = _verify_platform_versions(commits, "Cursor commit")
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "platform_info": platform_info},
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["platform_info"][platform]["downloadUrl"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: self.get_download_url(p, info) for p in self.PLATFORMS}
        return self._build_result_with_urls(
            info, hashes, urls, commit=info.metadata["commit"]
        )


go_vendor_updater("axiom-cli")
go_vendor_updater("beads")
go_vendor_updater("crush")
go_vendor_updater("gogcli")
# codex uses crane (not rustPlatform.buildRustPackage), so there is no
# cargoHash to compute — crane derives deps from the lockfile directly.
# The flake input ref is still updated via the refs phase.
npm_deps_updater("gemini-cli")
deno_deps_updater("linear-cli")
bun_node_modules_updater("opencode")
