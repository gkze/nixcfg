"""Updater for Mole's complete fixed-output source closure."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, SourceEntry, SourceHashes
from lib.update import process as update_process
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
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

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the pinned release tag to its current immutable commit."""
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
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the script source and both immutable helper archives together."""
        _ = (session, context)
        source_url = self._source_url(info)
        platform_urls = self._platform_urls(info)
        urls = [source_url, *platform_urls.values()]
        hash_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            update_process.compute_url_hashes(self.name, urls, config=self.config),
            hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        hashes_by_url = require_value(hash_drain, "Missing Mole source hashes")
        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("srcHash", hashes_by_url[source_url], url=source_url),
                *[
                    HashEntry.create(
                        "sha256",
                        hashes_by_url[url],
                        platform=platform,
                    )
                    for platform, url in sorted(platform_urls.items())
                ],
            ],
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist every URL and hash consumed by the Mole derivation."""
        return self._build_result_with_urls(
            info,
            hashes,
            self._platform_urls(info),
            commit=self._require_commit(info),
        )
