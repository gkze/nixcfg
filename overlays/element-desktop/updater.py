"""Updater for pinned element-desktop source and offline cache hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib.nix.models.sources import HashEntry, SourceEntry
from lib.update import sources as update_sources
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
)
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
    _build_fetch_yarn_deps_expr,
    compute_fixed_output_hash,
)
from lib.update.updaters.base import (
    HashEntryUpdater,
    UpdateContext,
    VersionInfo,
    package_dir_for,
    register_updater,
)
from lib.update.updaters.metadata import NO_METADATA


@register_updater
class ElementDesktopUpdater(HashEntryUpdater):
    """Refresh hashes for the currently pinned element-desktop release."""

    name = "element-desktop"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Read the pinned version from this package's ``sources.json``."""
        _ = session
        pkg_dir = package_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        entry = update_sources.load_source_entry(pkg_dir / "sources.json")
        version = entry.version
        if not isinstance(version, str) or not version:
            msg = "element-desktop sources.json is missing a pinned version"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata=NO_METADATA)

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes for pinned versions before equality checks."""
        _ = (context, info)
        return False

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "element-hq",
            "element-desktop",
            rev=f"v{version}",
        )

    @staticmethod
    def _offline_expr(version: str, src_hash: str) -> str:
        src_expr = _build_fetch_from_github_call(
            "element-hq",
            "element-desktop",
            rev=f"v{version}",
            hash_value=src_hash,
        )
        return _build_fetch_yarn_deps_expr(src_expr)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute srcHash first, then sha256 for fetchYarnDeps offline cache."""
        _ = (session, context)

        src_hash: str | None = None
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            error="Missing srcHash output",
        ):
            if isinstance(item, CapturedValue):
                payload = item.captured
                if not isinstance(payload, str):
                    msg = f"Expected srcHash string, got {type(payload)}"
                    raise TypeError(msg)
                src_hash = payload
            else:
                yield item

        if src_hash is None:
            msg = "Missing srcHash output"
            raise RuntimeError(msg)

        offline_hash: str | None = None
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                self._offline_expr(info.version, src_hash),
                config=self.config,
            ),
            error="Missing sha256 output",
        ):
            if isinstance(item, CapturedValue):
                payload = item.captured
                if not isinstance(payload, str):
                    msg = f"Expected sha256 string, got {type(payload)}"
                    raise TypeError(msg)
                offline_hash = payload
            else:
                yield item

        if offline_hash is None:
            msg = "Missing sha256 output"
            raise RuntimeError(msg)

        entries = [
            HashEntry.create("srcHash", src_hash),
            HashEntry.create("sha256", offline_hash),
        ]
        yield UpdateEvent.value(self.name, entries)
