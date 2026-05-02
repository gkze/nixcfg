"""Shared helpers for updaters that hash one resolved download URL."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.updaters.core import HashEntryUpdater, UpdateContext
from lib.update.updaters.dependencies import updater_dependencies
from lib.update.updaters.metadata import VersionInfo, metadata_get_str

if TYPE_CHECKING:
    import aiohttp

_dependencies = updater_dependencies()


async def stream_single_url_hash_entry(
    source_name: str,
    url: str,
    *,
    error: str = "Missing hash output",
) -> EventStream:
    """Hash one URL and emit a single sha256 :class:`HashEntry` with that URL."""
    hash_drain = ValueDrain[dict[str, str]]()
    async for event in drain_value_events(
        _dependencies.compute_url_hashes(source_name, [url]),
        hash_drain,
        parse=expect_hash_mapping,
    ):
        yield event
    hashes_by_url = require_value(hash_drain, error)
    yield UpdateEvent.value(
        source_name,
        [HashEntry.create("sha256", hashes_by_url[url], url=url)],
    )


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
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute a single sha256 entry for the resolved download URL."""
        _ = (session, context)
        async for event in stream_single_url_hash_entry(
            self.name,
            self.get_download_url(info),
        ):
            yield event


__all__ = ["SingleURLHashEntryUpdater", "stream_single_url_hash_entry"]
