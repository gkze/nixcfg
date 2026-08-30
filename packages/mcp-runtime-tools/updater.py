"""Updater for exact npm and PyPI package specs used by MCP runtimes."""

import asyncio
from typing import TYPE_CHECKING, ClassVar, override
from urllib.parse import quote

from lib import json_utils
from lib.nix.models.sources import HashCollection, SourceEntry, SourceHashes
from lib.update.events import EventStream, UpdateEvent
from lib.update.net import fetch_json
from lib.update.updaters import UpdateContext, Updater, VersionInfo, register_updater
from lib.update.updaters.metadata import metadata_as_mapping

if TYPE_CHECKING:
    import aiohttp


@register_updater
class McpRuntimeToolsUpdater(Updater):
    """Refresh the exact registry specs consumed by MCP launch wrappers."""

    name = "mcp-runtime-tools"
    required_tools: ClassVar[tuple[str, ...]] = ()
    _NPM_PACKAGES: ClassVar[tuple[str, ...]] = (
        "@padenot/firefox-devtools-mcp",
        "@steipete/macos-automator-mcp",
        "@vantasdk/vanta-mcp-server",
        "chrome-devtools-mcp",
        "convex",
        "mcp-remote",
        "next-devtools-mcp",
        "slack-mcp-server",
    )
    _PYPI_PACKAGES: ClassVar[tuple[str, ...]] = (
        "markitdown-mcp",
        "mcp-proxy-for-aws",
    )

    async def _fetch_npm_pin(
        self,
        session: aiohttp.ClientSession,
        package: str,
    ) -> tuple[str, str]:
        url = f"https://registry.npmjs.org/{quote(package, safe='')}/latest"
        payload = await fetch_json(
            session,
            url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        metadata = json_utils.as_object_dict(payload, context=url)
        version = json_utils.get_required_str(metadata, "version", context=url)
        if not version:
            msg = f"Empty npm version in {url}"
            raise RuntimeError(msg)
        return package, f"{package}@{version}"

    async def _fetch_pypi_pin(
        self,
        session: aiohttp.ClientSession,
        package: str,
    ) -> tuple[str, str]:
        url = f"https://pypi.org/pypi/{package}/json"
        payload = await fetch_json(
            session,
            url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        metadata = json_utils.as_object_dict(payload, context=url)
        info = json_utils.as_object_dict(metadata.get("info"), context=f"{url} info")
        version = json_utils.get_required_str(info, "version", context=f"{url} info")
        if not version:
            msg = f"Empty PyPI version in {url}"
            raise RuntimeError(msg)
        return package, f"{package}=={version}"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve every tracked package from its authoritative registry."""
        resolved = await asyncio.gather(
            *(self._fetch_npm_pin(session, package) for package in self._NPM_PACKAGES),
            *(
                self._fetch_pypi_pin(session, package)
                for package in self._PYPI_PACKAGES
            ),
        )
        return VersionInfo(
            version="registry",
            metadata={"pins": dict(sorted(resolved))},
        )

    @staticmethod
    def _pins(info: VersionInfo) -> dict[str, str]:
        metadata = metadata_as_mapping(info.metadata, context="MCP runtime metadata")
        raw_pins = metadata.get("pins")
        if not isinstance(raw_pins, dict):
            msg = "Expected MCP runtime pins mapping"
            raise TypeError(msg)
        pins: dict[str, str] = {}
        for name, spec in raw_pins.items():
            if not isinstance(name, str) or not isinstance(spec, str):
                msg = "Expected MCP runtime pins to contain only strings"
                raise TypeError(msg)
            pins[name] = spec
        return pins

    @override
    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        current = context.current if isinstance(context, UpdateContext) else context
        return (
            current is not None
            and current.version == info.version
            and current.pins == self._pins(info)
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Emit an empty hash mapping because registry specs are the artifacts."""
        _ = (info, session, context)
        yield UpdateEvent.value(self.name, {})

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist resolved package specs through the common source model."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            pins=self._pins(info),
        )
