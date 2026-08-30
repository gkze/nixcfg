"""Updater for Mole's complete fixed-output source closure."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
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
    PinnedSourceDownloadUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class MoleAppUpdater(PinnedSourceDownloadUpdater):
    """Pinned updater for Mole's script source and helper binaries."""

    name = "mole-app"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-amd64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://github.com/tw93/Mole/releases/download/"
        "V{version}/binaries-{platform_value}.tar.gz"
    )

    @staticmethod
    def _source_url(info: VersionInfo) -> str:
        return f"https://github.com/tw93/Mole/archive/refs/tags/V{info.version}.tar.gz"

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
        return SourceEntry(
            version=info.version,
            urls=self._platform_urls(info),
            hashes=HashCollection.from_value(hashes),
        )
