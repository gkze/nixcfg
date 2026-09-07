"""Updater for Mole's complete fixed-output source closure."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, SourceEntry, SourceHashes
from lib.update import process as update_process
from lib.update.events import (
    EventSink,
    ignore_event,
)
from lib.update.updaters import (
    GitHubReleaseUpdater,
    PinnedSourceDownloadUpdater,
    UpdateContext,
    VersionInfo,
    read_pinned_source_version,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class MoleAppUpdater(GitHubReleaseUpdater, PinnedSourceDownloadUpdater):
    """Pinned updater for Mole's script source and helper binaries."""

    name = "mole-app"
    GITHUB_OWNER = "tw93"
    GITHUB_REPO = "Mole"
    TAG_PREFIX = "V"
    RESOLVE_TAG_COMMIT = True
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-amd64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://github.com/tw93/Mole/releases/download/"
        "V{version}/binaries-{platform_value}.tar.gz"
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the pinned release tag to its current immutable commit."""
        _ = context
        version = read_pinned_source_version(self.name)
        tag = f"{self.TAG_PREFIX}{version}"
        commit = await self._resolve_release_tag_commit(session, tag)
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "tag": tag},
        )

    def _source_url(self, info: VersionInfo) -> str:
        commit = self._require_commit(info)
        return (
            f"https://github.com/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/"
            f"archive/{commit}.tar.gz"
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Hash the script source and both immutable helper archives together."""
        _ = (session, context)
        source_url = self._source_url(info)
        platform_urls = self._platform_urls(info)
        urls = [source_url, *platform_urls.values()]
        hashes_by_url = await update_process.compute_url_hashes(
            self.name, urls, config=self.config, emit=emit
        )
        return [
            HashEntry.create("srcHash", hashes_by_url[source_url], url=source_url),
            *[
                HashEntry.create(
                    "sha256",
                    hashes_by_url[url],
                    platform=platform,
                )
                for platform, url in sorted(platform_urls.items())
            ],
        ]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist every URL and hash consumed by the Mole derivation."""
        return self._build_result_with_urls(
            info,
            hashes,
            self._platform_urls(info),
            commit=self._require_commit(info),
        )
