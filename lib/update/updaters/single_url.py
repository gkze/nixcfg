"""Shared helpers for updaters that hash one resolved download URL."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update import process as update_process
from lib.update.events import (
    EventSink,
    ignore_event,
)
from lib.update.updaters.core import HashEntryUpdater, UpdateContext
from lib.update.updaters.metadata import VersionInfo, metadata_get_str

if TYPE_CHECKING:
    import aiohttp

    from lib.update.config import UpdateConfig


async def stream_single_url_hash_entry(
    source_name: str,
    url: str,
    *,
    config: UpdateConfig,
    emit: EventSink = ignore_event,
) -> list[HashEntry]:
    """Hash one URL and emit a single sha256 :class:`HashEntry` with that URL."""
    hashes_by_url = await update_process.compute_url_hashes(
        source_name, [url], config=config, emit=emit
    )
    return [HashEntry.create("sha256", hashes_by_url[url], url=url)]


class SingleURLHashEntryUpdater(HashEntryUpdater):
    """Hash-entry updater for metadata that carries one resolved download URL."""

    URL_METADATA_KEY: ClassVar[str] = "url"
    URL_METADATA_LABEL: ClassVar[str] = "download URL"

    def get_download_url(self, info: VersionInfo) -> str:
        """Return the resolved download URL from ``info.metadata``."""
        metadata = info.metadata
        url = metadata_get_str(
            metadata,
            self.URL_METADATA_KEY,
            context=f"{self.name} metadata",
        )
        if not url:
            msg = f"Missing {self.URL_METADATA_LABEL} metadata for {self.name}: {metadata!r}"
            raise RuntimeError(msg)
        return url

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Compute a single sha256 entry for the resolved download URL."""
        _ = (session, context)
        return await stream_single_url_hash_entry(
            self.name, self.get_download_url(info), config=self.config, emit=emit
        )


__all__ = ["SingleURLHashEntryUpdater", "stream_single_url_hash_entry"]
