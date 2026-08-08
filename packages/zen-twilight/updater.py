"""Updater for the Zen Twilight channel DMG."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

from lib.nix.models.sources import SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_source_hashes,
    require_value,
)
from lib.update.net import fetch_url
from lib.update.updaters import (
    DownloadHashUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext


class _TwilightSnapshotChangedError(RuntimeError):
    """Signal that the mutable channel moved around one hash attempt."""

    def __init__(self, *, phase: str, expected: str, observed: str) -> None:
        self.phase = phase
        self.expected = expected
        self.observed = observed
        super().__init__(
            f"Twilight channel changed {phase} hashing: "
            f"expected {expected}, observed {observed}"
        )


@register_updater
class ZenTwilightUpdater(DownloadHashUpdater):
    """Track the mutable Twilight channel DMG and its published build metadata."""

    name = "zen-twilight"
    SNAPSHOT_ATTEMPTS = 2
    TWILIGHT_UPDATE_URL = (
        "https://updates.zen-browser.app/updates/browser/"
        "Darwin_aarch64-gcc3/twilight/update.xml"
    )
    TWILIGHT_DMG_URL = (
        "https://github.com/zen-browser/desktop/releases/download/"
        "twilight-1/zen.macos-universal.dmg"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": TWILIGHT_DMG_URL,
        "x86_64-darwin": TWILIGHT_DMG_URL,
    }
    supported_platforms: ClassVar[tuple[str, ...]] = tuple(PLATFORMS)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.aarch64-darwin.zen-twilight",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Combine Twilight's published app version and build ID."""
        payload = await fetch_url(
            session,
            self.TWILIGHT_UPDATE_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            snippet = payload[:120].decode(errors="replace")
            msg = (
                f"Invalid Twilight update XML from {self.TWILIGHT_UPDATE_URL}: "
                f"{snippet}"
            )
            raise RuntimeError(msg) from exc

        update = root.find("./update")
        if update is None:
            msg = f"No update found in {self.TWILIGHT_UPDATE_URL}"
            raise RuntimeError(msg)
        app_version = require_version(
            update.attrib.get("appVersion"),
            context=self.TWILIGHT_UPDATE_URL,
        )
        build_id = require_version(
            update.attrib.get("buildID"),
            context=self.TWILIGHT_UPDATE_URL,
        )
        return VersionInfo(version=f"{app_version}-{build_id}")

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash only a channel artifact bracketed by matching feed snapshots."""
        before = await self.fetch_latest(session)
        if before != info:
            raise _TwilightSnapshotChangedError(
                phase="before",
                expected=info.version,
                observed=before.version,
            )

        hashes_drain = ValueDrain[SourceHashes]()
        async for event in drain_value_events(
            super().fetch_hashes(
                info,
                session,
                context=context,
            ),
            hashes_drain,
            parse=expect_source_hashes,
        ):
            yield event
        after = await self.fetch_latest(session)
        if after != info:
            raise _TwilightSnapshotChangedError(
                phase="after",
                expected=info.version,
                observed=after.version,
            )

        hashes = require_value(hashes_drain, "Missing hash output")
        yield UpdateEvent.value(self.name, hashes)

    async def update_stream(
        self,
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
        *,
        pinned_version: VersionInfo | None = None,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Retry an unpinned update from version resolution after channel movement."""
        for attempt in range(
            1, self.SNAPSHOT_ATTEMPTS + 1
        ):  # pragma: no branch -- every attempt returns, raises, or retries
            try:
                async for event in super().update_stream(
                    current,
                    session,
                    pinned_version=pinned_version,
                    context=context,
                ):
                    yield event
            except _TwilightSnapshotChangedError as exc:
                if pinned_version is not None:
                    msg = (
                        f"Pinned Twilight version {pinned_version.version} no longer "
                        f"matches the channel ({exc.observed}); rerun version "
                        "resolution before computing hashes"
                    )
                    raise RuntimeError(msg) from exc
                if attempt >= self.SNAPSHOT_ATTEMPTS:
                    msg = (
                        "Twilight channel changed repeatedly while hashing; "
                        "rerun the updater after publication settles"
                    )
                    raise RuntimeError(msg) from exc
                next_attempt = attempt + 1
                yield UpdateEvent.status(
                    self.name,
                    "Twilight channel changed while hashing; resolving a fresh "
                    "snapshot...",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.RETRY,
                        value=f"attempt {next_attempt}/{self.SNAPSHOT_ATTEMPTS}",
                    ),
                )
                await asyncio.sleep(max(0.0, self.config.default_retry_backoff))
            else:
                return

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes for the pinned channel artifact before comparing."""
        _ = (context, info)
        return False
