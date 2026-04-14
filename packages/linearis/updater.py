"""Updater for the published linearis npm tarball."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib import json_utils
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
    expect_hash_mapping,
)
from lib.update.net import fetch_json
from lib.update.process import compute_url_hashes
from lib.update.updaters.base import HashEntryUpdater, UpdateContext, VersionInfo
from lib.update.updaters.metadata import metadata_get_str
from lib.update.updaters.registry import register_updater


@register_updater
class LinearisUpdater(HashEntryUpdater):
    """Track the published npm tarball instead of GitHub ``main``."""

    name = "linearis"
    LATEST_URL = "https://registry.npmjs.org/linearis/latest"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest published version and tarball URL from npm."""
        payload = await fetch_json(
            session,
            self.LATEST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        payload_map = json_utils.as_object_dict(payload, context=self.LATEST_URL)
        try:
            version = json_utils.get_required_str(
                payload_map,
                "version",
                context=self.LATEST_URL,
            )
        except TypeError as exc:
            msg = f"Missing version in npm metadata from {self.LATEST_URL}"
            raise RuntimeError(msg) from exc
        if not version:
            msg = f"Missing version in npm metadata from {self.LATEST_URL}"
            raise RuntimeError(msg)

        dist = payload_map.get("dist")
        try:
            dist_map = json_utils.as_object_dict(
                dist,
                context=f"{self.LATEST_URL} dist",
            )
            tarball = json_utils.get_required_str(
                dist_map,
                "tarball",
                context=f"{self.LATEST_URL} dist",
            )
        except TypeError as exc:
            msg = f"Missing dist.tarball in npm metadata from {self.LATEST_URL}"
            raise RuntimeError(msg) from exc
        if not tarball:
            msg = f"Missing dist.tarball in npm metadata from {self.LATEST_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata={"tarball": tarball})

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute a single sha256 entry for the published npm tarball."""
        _ = (session, context)
        metadata = info.metadata
        tarball = metadata_get_str(metadata, "tarball", context=f"{self.name} metadata")
        if not tarball:
            msg = f"Missing tarball metadata for {self.name}: {metadata!r}"
            raise RuntimeError(msg)

        async for item in capture_stream_value(
            compute_url_hashes(self.name, [tarball]),
            error="Missing hash output",
        ):
            if isinstance(item, CapturedValue):
                hash_mapping = expect_hash_mapping(item.captured)
                hash_value = hash_mapping[tarball]
                yield UpdateEvent.value(
                    self.name,
                    [HashEntry.create("sha256", hash_value, url=tarball)],
                )
            else:
                yield item
