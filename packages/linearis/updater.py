"""Updater for the published linearis npm tarball."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import SingleURLHashEntryUpdater, VersionInfo
from lib.update.updaters.metadata import DownloadUrlMetadata
from lib.update.updaters.registry import register_updater


@register_updater
class LinearisUpdater(SingleURLHashEntryUpdater):
    """Track the published npm tarball instead of GitHub ``main``."""

    name = "linearis"
    LATEST_URL = "https://registry.npmjs.org/linearis/latest"
    URL_METADATA_LABEL = "tarball"

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
        return VersionInfo(version=version, metadata=DownloadUrlMetadata(url=tarball))
