"""Updater for Codex flake metadata and its complete fixed-output closure."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import process as update_process
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.updaters import (
    Crate2NixMetadataUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream


@register_updater
class CodexUpdater(Crate2NixMetadataUpdater):
    """Track Codex's flake input, crate graph, and prebuilt WebRTC inputs."""

    name = "codex"
    source_pins: ClassVar[dict[str, str]] = {"clangResourceVersion": "23"}
    WEBRTC_RELEASE: ClassVar[str] = "webrtc-24f6822-2"
    WEBRTC_ASSETS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "webrtc-mac-arm64-release.zip",
        "x86_64-linux": "webrtc-linux-x64-release.zip",
    }

    @classmethod
    def _webrtc_urls(cls) -> dict[str, str]:
        base = (
            "https://github.com/livekit/rust-sdks/releases/download/"
            f"{cls.WEBRTC_RELEASE}"
        )
        return {
            platform: f"{base}/{asset}" for platform, asset in cls.WEBRTC_ASSETS.items()
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist V8 build compatibility metadata with the locked input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
            pins=self.source_pins,
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Require updater-owned WebRTC URL identities to remain current."""
        if not await super()._is_latest(context, info):
            return False
        current = context.current if isinstance(context, UpdateContext) else context
        if current is None or current.hashes.entries is None:
            return False
        current_urls = sorted(
            (entry.platform, entry.url)
            for entry in current.hashes.entries
            if entry.hash_type == "sha256"
            and entry.platform is not None
            and entry.url is not None
        )
        return current_urls == sorted(self._webrtc_urls().items())

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh crate2nix artifacts and hash both supported WebRTC archives."""
        _ = session
        async for event in self.stream_materialized_artifacts(
            source_overrides=self.materialization_source_overrides(
                info,
                context=context,
            )
        ):
            yield event

        urls = self._webrtc_urls()
        ordered_urls = [urls[platform] for platform in sorted(urls)]
        hash_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            update_process.compute_url_hashes(
                self.name,
                ordered_urls,
                config=self.config,
            ),
            hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        hashes_by_url = require_value(hash_drain, "Missing Codex WebRTC hashes")
        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create(
                    "sha256",
                    hashes_by_url[urls[platform]],
                    platform=platform,
                    url=urls[platform],
                )
                for platform in sorted(urls)
            ],
        )
