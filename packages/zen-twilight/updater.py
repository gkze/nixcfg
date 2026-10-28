"""Updater for the Zen Twilight channel DMG."""

import asyncio
from functools import partial
from typing import TYPE_CHECKING, ClassVar

from defusedxml import ElementTree

from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ignore_event,
)
from lib.update.net import fetch_url
from lib.update.updaters import (
    DownloadHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.vendor_feeds import require_version

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes


def _twilight_build_id(version: str) -> str:
    """Return the numeric build id from ``appVersion-buildID``."""
    build_id = version.rpartition("-")[2]
    if not build_id.isdigit() or build_id == version:
        msg = f"Twilight version {version!r} has no numeric build id"
        raise RuntimeError(msg)
    return build_id


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

    def __reduce__(self) -> tuple[partial[_TwilightSnapshotChangedError], tuple[()]]:
        """Retain the typed retry signal across DBOS step replay."""
        return partial(
            type(self), phase=self.phase, expected=self.expected, observed=self.observed
        ), ()


@register_updater
class ZenTwilightUpdater(DownloadHashUpdater):
    """Track the mutable Twilight channel DMG and its published build metadata."""

    name = "zen-twilight"
    SNAPSHOT_ATTEMPTS = 2
    # A stale edge can answer with the previous build after the feed has moved.
    # Ignore a few of those reads before treating the snapshot as unconfirmed.
    STALE_READS = 3
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
    # Recompute hashes for the pinned channel artifact before comparing.
    always_rehash = True

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Combine Twilight's published app version and build ID."""
        _ = context
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

    async def _observe_snapshot(
        self,
        session: aiohttp.ClientSession,
        context: UpdateContext,
        info: VersionInfo,
        *,
        phase: str,
    ) -> None:
        """Require a live read of ``info``, ignoring older cached builds.

        A newer build is a real channel move. An older build is a stale edge:
        Update 40593795688 resolved ``1.24t`` from the resolve checkpoint, then
        the next live read was already ``1.25t``.
        """
        stale_reads = 0
        while True:
            observed = await self.fetch_latest(session, context=context)
            if observed == info:
                return
            if _twilight_build_id(observed.version) > _twilight_build_id(info.version):
                raise _TwilightSnapshotChangedError(
                    phase=phase,
                    expected=info.version,
                    observed=observed.version,
                )
            stale_reads += 1
            if stale_reads >= self.STALE_READS:
                raise _TwilightSnapshotChangedError(
                    phase=phase,
                    expected=info.version,
                    observed=observed.version,
                )
            await asyncio.sleep(max(0.0, self.config.default_retry_backoff))

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Hash only a channel artifact bracketed by matching feed snapshots."""
        await self._observe_snapshot(session, context, info, phase="before")
        hashes = await super().fetch_hashes(info, session, context=context, emit=emit)
        await self._observe_snapshot(session, context, info, phase="after")
        return hashes

    async def update_stream(
        self,
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | None = None,
        emit: EventSink = ignore_event,
    ) -> SourceEntry | None:
        """Resolve a fresh version and retry after channel movement.

        The resolve step is checkpointed, so a retry would otherwise keep
        hashing the first build. A newer live read replaces that target.
        """
        context = context if context is not None else UpdateContext(current=current)
        attempt = 1
        while True:
            try:
                return await super().update_stream(
                    current, session, context=context, emit=emit
                )
            except _TwilightSnapshotChangedError as exc:
                if _twilight_build_id(exc.observed) > _twilight_build_id(exc.expected):
                    context.resolved_version = VersionInfo(version=exc.observed)
                if attempt >= self.SNAPSHOT_ATTEMPTS:
                    msg = (
                        "Twilight channel changed repeatedly while hashing; "
                        f"expected {exc.expected}, observed {exc.observed}"
                    )
                    raise RuntimeError(msg) from exc
                next_attempt = attempt + 1
                await emit(
                    UpdateEvent.status(
                        self.name,
                        "Twilight channel changed while hashing; resolving a fresh "
                        "snapshot...",
                        operation="compute_hash",
                        status=StatusInfo(
                            kind=StatusKind.RETRY,
                            value=f"attempt {next_attempt}/{self.SNAPSHOT_ATTEMPTS}",
                        ),
                    )
                )
                await asyncio.sleep(max(0.0, self.config.default_retry_backoff))
                attempt += 1
