"""Updater for Docker Desktop."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import (
    DownloadUrlMetadataUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    import aiohttp


@register_updater
class DockerDesktopUpdater(DownloadUrlMetadataUpdater):
    """Resolve Docker Desktop from Docker's appcast JSON."""

    name = "docker-desktop"
    APPCAST_URL = "https://desktop.docker.com/mac/main/arm64/appcast.json"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    URL_METADATA_CONTEXT = "Docker Desktop metadata"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest Docker Desktop build and DMG URL."""
        payload = await fetch_json(
            session,
            self.APPCAST_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        data = json_utils.as_object_dict(payload, context=self.APPCAST_URL)
        items = json_utils.as_object_list(
            data.get("Items"),
            context=f"{self.APPCAST_URL} Items",
        )
        if not items:
            msg = f"No Docker Desktop items found in {self.APPCAST_URL}"
            raise RuntimeError(msg)
        item = json_utils.as_object_dict(items[0], context=f"{self.APPCAST_URL} item")
        app_version = json_utils.get_required_str(
            item,
            "AppVersion",
            context=f"{self.APPCAST_URL} item",
        )
        build_number = json_utils.get_required_str(
            item,
            "BuildNumber",
            context=f"{self.APPCAST_URL} item",
        )
        artifacts = json_utils.as_object_list(
            item.get("Artifacts"),
            context=f"{self.APPCAST_URL} Artifacts",
        )
        for artifact in artifacts:
            artifact_map = json_utils.as_object_dict(
                artifact,
                context=f"{self.APPCAST_URL} artifact",
            )
            if artifact_map.get("Type") != "dmg":
                continue
            url = json_utils.get_required_str(
                artifact_map,
                "URL",
                context=f"{self.APPCAST_URL} artifact",
            )
            return VersionInfo(
                version=f"{app_version}-{build_number}",
                metadata=DownloadUrlMetadata(url=url),
            )
        msg = f"No Docker Desktop DMG artifact found in {self.APPCAST_URL}"
        raise RuntimeError(msg)
