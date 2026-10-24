"""Resolve Reason (formerly Ara) through its official desktop update feed."""

from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import EventSink, ignore_event
from lib.update.net import fetch_json, fetch_redirect_location
from lib.update.process import compute_sri_hash
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceHashes
    from lib.update.updaters import UpdateContext


def _versioned_object_version(location: str, prefix: str) -> str:
    """Return the /desktop/stable/<ver>/ segment Nix will GET, or empty."""
    start = location.find(prefix)
    if start < 0:
        return ""
    version, slash, _tail = location[start + len(prefix) :].partition("/")
    if not slash or not version:
        return ""
    return version


@register_updater
class ReasonUpdater(DownloadHashUpdater):
    """Pin Reason's latest-only Darwin desktop download."""

    name = "reason"
    FEED_URL = (
        "https://reasonmachines.com/api/desktop-update"
        "?target=darwin&arch=aarch64&current_version=0.0.0&channel=stable"
    )
    # The vendor only exposes signed release URLs. Persist its stable redirect
    # instead; Nix pins the bytes, and the package verifies their bundle version.
    # The redirect is latest-only (Update 37385596458 hashed 0.1.71, then
    # fetched different bytes). Signed Tigris URLs expire in 900s; unsigned
    # object keys 403. The fetcher reuses this public URL and fail-closes
    # unless Location is /desktop/stable/<pin>/. The object path is the pin:
    # x-ara-desktop-version can lag it (Update 38214877123: header 0.1.64,
    # Location 0.1.84).
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://reasonmachines.com/api/desktop-download?arch=aarch64"
    }
    VERSIONED_OBJECT_PREFIX: ClassVar[str] = "/desktop/stable/"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Read the release version without persisting expiring signed URLs."""
        _ = context
        payload = json_utils.as_object_dict(
            await fetch_json(session, self.FEED_URL, config=self.config),
            context="Reason desktop feed",
        )
        json_utils.get_required_str(payload, "name", context="Reason desktop feed")
        # The public redirect can race ahead of the feed (Update 37037587993
        # hashed 0.1.66 bytes while the feed still named 0.1.64) and ahead of
        # x-ara-desktop-version (Update 38214877123: header 0.1.64, Location
        # 0.1.84). Pin the version of the bytes Nix will GET. That version is
        # the /desktop/stable/<ver>/ object on the public 307; the signed
        # Location authorizes GET only, so a following HEAD returns HTTP 403
        # (Update 37067759340).
        download_url = self.PLATFORMS["aarch64-darwin"]
        location = await fetch_redirect_location(
            session, download_url, config=self.config
        )
        pin_version = _versioned_object_version(location, self.VERSIONED_OBJECT_PREFIX)
        if not pin_version:
            msg = f"Reason download Location is not a versioned object: {location}"
            raise RuntimeError(msg)
        return VersionInfo(version=pin_version)

    def fod_name(self, version: str) -> str:
        """Match fetch-dmg.nix so prefetch lands on the FOD output path."""
        return f"Reason_{version}_aarch64.dmg"

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Prefetch under the FOD name so later jobs substitute instead of re-GET.

        Update 38215834334 hashed ``desktop-download`` then cascade rebuilt
        ``Reason_0.1.84_aarch64.dmg`` after latest moved to 0.1.85.
        """
        _ = (session, context)
        url = self.get_download_url("aarch64-darwin", info)
        hash_value = await compute_sri_hash(
            self.name,
            url,
            name=self.fod_name(info.version),
            config=self.config,
            emit=emit,
        )
        return {"aarch64-darwin": hash_value}
