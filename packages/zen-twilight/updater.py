"""Updater for the Zen Twilight channel DMG.

Zen publishes Twilight by replacing the assets on the single ``twilight-1``
release. ``releases/download/twilight-1/zen.macos-universal.dmg`` therefore
changes bytes without changing URL, which is why a pinned hash drifts between
Updates. The release asset id is not reused: the next build deletes it and
uploads a new one. The pin stores that asset's API URL. ``fetchurl`` follows
it only when it sends ``Accept: application/octet-stream``. The hash is the
pipeline prefetch of the bytes whose GitHub digest matches that asset, so the
stored URL and the stored hash name one upload.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from functools import partial
from pathlib import Path
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
from lib.update.net import fetch_github_api, fetch_url
from lib.update.updaters import (
    DownloadHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_url_hash_mapping,
)
from lib.update.updaters.metadata import DownloadUrlMetadata, metadata_get_str
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


def twilight_asset_url(asset_id: int) -> str:
    """Return the GitHub API URL for one Twilight DMG upload."""
    if asset_id < 1:
        msg = f"Twilight release asset id must be positive, got {asset_id}"
        raise RuntimeError(msg)
    return (
        f"https://api.github.com/repos/zen-browser/desktop/releases/assets/{asset_id}"
    )


class _TwilightSnapshotChangedError(RuntimeError):
    """Signal that the mutable channel moved around one hash attempt."""

    def __init__(
        self,
        *,
        phase: str,
        expected: str,
        observed: str,
        asset_url: str,
    ) -> None:
        self.phase = phase
        self.expected = expected
        self.observed = observed
        self.asset_url = asset_url
        super().__init__(
            f"Twilight channel changed {phase} hashing: "
            f"expected {expected}, observed {observed}"
        )

    def __reduce__(self) -> tuple[partial[_TwilightSnapshotChangedError], tuple[()]]:
        """Retain the typed retry signal across DBOS step replay."""
        return partial(
            type(self),
            phase=self.phase,
            expected=self.expected,
            observed=self.observed,
            asset_url=self.asset_url,
        ), ()


@dataclass(frozen=True, slots=True)
class _TwilightAsset:
    """One ``zen.macos-universal.dmg`` upload on the Twilight release."""

    asset_id: int
    digest: str
    browser_download_url: str

    @property
    def api_url(self) -> str:
        """Return the URL stored in ``sources.json``."""
        return twilight_asset_url(self.asset_id)


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@register_updater
class ZenTwilightUpdater(DownloadHashUpdater):
    """Track one Twilight DMG upload, not the floating ``twilight-1`` URL."""

    name = "zen-twilight"
    SNAPSHOT_ATTEMPTS = 2
    # A stale edge can answer with the previous build after the feed has moved.
    # Ignore a few of those reads before treating the snapshot as unconfirmed.
    STALE_READS = 3
    TWILIGHT_UPDATE_URL = (
        "https://updates.zen-browser.app/updates/browser/"
        "Darwin_aarch64-gcc3/twilight/update.xml"
    )
    # The channel Zen rewrites. Stored pins use the release asset id instead.
    TWILIGHT_DMG_URL = (
        "https://github.com/zen-browser/desktop/releases/download/"
        "twilight-1/zen.macos-universal.dmg"
    )
    TWILIGHT_RELEASE_API = "repos/zen-browser/desktop/releases/tags/twilight-1"
    TWILIGHT_ASSET_NAME = "zen.macos-universal.dmg"
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

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return the release-asset URL resolved for this snapshot."""
        _ = platform
        url = metadata_get_str(info.metadata, "url")
        if url is None:
            msg = "Twilight asset URL was not resolved"
            raise RuntimeError(msg)
        return url

    async def _resolve_asset(self, session: aiohttp.ClientSession) -> _TwilightAsset:
        """Return the current DMG upload on the Twilight release."""
        payload = await fetch_github_api(
            session,
            self.TWILIGHT_RELEASE_API,
            config=self.config,
        )
        if not isinstance(payload, dict):
            msg = f"Twilight release {self.TWILIGHT_RELEASE_API} is not an object"
            raise TypeError(msg)
        assets = payload.get("assets")
        if not isinstance(assets, list):
            msg = f"Twilight release {self.TWILIGHT_RELEASE_API} has no assets"
            raise TypeError(msg)
        matches: list[_TwilightAsset] = []
        for asset in assets:
            if (
                not isinstance(asset, dict)
                or asset.get("name") != self.TWILIGHT_ASSET_NAME
            ):
                continue
            asset_id = asset.get("id")
            digest = asset.get("digest")
            download_url = asset.get("browser_download_url")
            if not isinstance(asset_id, int) or isinstance(asset_id, bool):
                msg = f"Twilight asset {self.TWILIGHT_ASSET_NAME} has no id"
                raise TypeError(msg)
            if not isinstance(digest, str) or not digest.startswith("sha256:"):
                msg = (
                    f"Twilight asset {asset_id} has no sha256 digest; "
                    "the floating download URL cannot be pinned"
                )
                raise RuntimeError(msg)
            if (
                not isinstance(download_url, str)
                or not download_url.startswith("https://")
                or not download_url.endswith(f"/{self.TWILIGHT_ASSET_NAME}")
            ):
                msg = f"Twilight asset {asset_id} has no HTTPS DMG download"
                raise RuntimeError(msg)
            matches.append(
                _TwilightAsset(
                    asset_id=asset_id,
                    digest=digest,
                    browser_download_url=download_url,
                )
            )
        if len(matches) != 1:
            msg = (
                f"Twilight release has {len(matches)} {self.TWILIGHT_ASSET_NAME} assets"
            )
            raise RuntimeError(msg)
        return matches[0]

    async def _require_asset_digest(self, asset: _TwilightAsset) -> None:
        """Require the prefetched DMG to be the release asset's bytes."""
        from lib.nix.commands.hash import nix_prefetch_url_result  # noqa: PLC0415

        result = await nix_prefetch_url_result(
            asset.browser_download_url,
            name=self.TWILIGHT_ASSET_NAME,
            command_timeout=self.config.default_subprocess_timeout,
        )
        raw = _sha256_file(Path(result.storePath))
        if asset.digest != f"sha256:{raw}":
            msg = (
                f"Twilight DMG does not match release asset {asset.asset_id}: "
                f"expected {asset.digest}, got sha256:{raw}"
            )
            raise RuntimeError(msg)

    async def _snapshot_changed(
        self,
        session: aiohttp.ClientSession,
        *,
        phase: str,
        expected: str,
        observed: str,
    ) -> None:
        asset = await self._resolve_asset(session)
        raise _TwilightSnapshotChangedError(
            phase=phase,
            expected=expected,
            observed=observed,
            asset_url=asset.api_url,
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Combine Twilight's published app version, build ID, and asset id."""
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
        asset = await self._resolve_asset(session)
        return VersionInfo(
            version=f"{app_version}-{build_id}",
            metadata=DownloadUrlMetadata(url=asset.api_url),
        )

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
            if observed.version == info.version:
                info_url = metadata_get_str(info.metadata, "url")
                observed_url = metadata_get_str(observed.metadata, "url")
                if info_url is None or info_url == observed_url:
                    return
                await self._snapshot_changed(
                    session,
                    phase=phase,
                    expected=info.version,
                    observed=observed.version,
                )
            if _twilight_build_id(observed.version) > _twilight_build_id(info.version):
                await self._snapshot_changed(
                    session,
                    phase=phase,
                    expected=info.version,
                    observed=observed.version,
                )
            stale_reads += 1
            if stale_reads >= self.STALE_READS:
                await self._snapshot_changed(
                    session,
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
        """Hash the DMG only while its release asset and feed snapshot hold."""
        await self._observe_snapshot(session, context, info, phase="before")
        asset = await self._resolve_asset(session)
        pinned = metadata_get_str(info.metadata, "url")
        if pinned is not None and pinned != asset.api_url:
            await self._snapshot_changed(
                session,
                phase="before",
                expected=info.version,
                observed=info.version,
            )
        hashed = await stream_url_hash_mapping(
            self.name,
            dict.fromkeys(self.PLATFORMS, asset.browser_download_url),
            config=self.config,
            emit=emit,
        )
        await self._require_asset_digest(asset)
        await self._observe_snapshot(session, context, info, phase="after")
        if (await self._resolve_asset(session)).api_url != asset.api_url:
            await self._snapshot_changed(
                session,
                phase="after",
                expected=info.version,
                observed=info.version,
            )
        return hashed

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
        hashing the first build. A newer live read replaces that target and
        the asset URL captured with it.
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
                    context.resolved_version = VersionInfo(
                        version=exc.observed,
                        metadata=DownloadUrlMetadata(url=exc.asset_url),
                    )
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
