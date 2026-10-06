"""Resolve Reason (formerly Ara) through its official desktop update feed."""

from typing import TYPE_CHECKING, ClassVar

import aiohttp

from lib import json_utils
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import HTTP_BAD_REQUEST, fetch_json
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    from lib.update.updaters import UpdateContext


def _header(headers: dict[str, str], name: str) -> str:
    return next(
        (
            value.strip()
            for key, value in headers.items()
            if key.lower() == name and value.strip()
        ),
        "",
    )


@register_updater
class AraUpdater(DownloadHashUpdater):
    """Keep the existing package identity while following the vendor rebrand."""

    name = "ara"
    FEED_URL = (
        "https://reasonmachines.com/api/desktop-update"
        "?target=darwin&arch=aarch64&current_version=0.0.0&channel=stable"
    )
    # The vendor only exposes signed release URLs. Persist its stable redirect
    # instead; Nix pins the bytes, and the package verifies their bundle version.
    # The redirect is latest-only (Update 37385596458 hashed 0.1.71, then
    # fetched different bytes). Signed Tigris URLs expire in 900s; unsigned
    # object keys 403. The fetcher reuses this public URL and fail-closes
    # unless Location is /desktop/stable/<pin>/ and the version header matches.
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
        feed_version = json_utils.get_required_str(
            payload, "name", context="Reason desktop feed"
        )
        # The public redirect can race ahead of the feed (Update 37037587993
        # hashed 0.1.66 bytes while the feed still named 0.1.64). Pin the
        # version of the bytes Nix will hash. That version lives on the public
        # 307; the signed Location authorizes GET only, so a following HEAD
        # returns HTTP 403 (Update 37067759340).
        download_url = self.PLATFORMS["aarch64-darwin"]
        async with session.head(
            download_url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=self.config.default_timeout),
        ) as response:
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    f"Reason download discovery failed with HTTP {response.status} "
                    f"{response.reason}"
                )
                raise RuntimeError(msg)
            headers = dict(response.headers)
            download_version = _header(headers, "x-ara-desktop-version")
            pin_version = download_version or feed_version
            location = _header(headers, "location")
            expected_object = f"{self.VERSIONED_OBJECT_PREFIX}{pin_version}/"
            if expected_object not in location:
                msg = (
                    "Reason download Location is not the versioned object "
                    f"for {pin_version}: {location or '<missing>'}"
                )
                raise RuntimeError(msg)
        return VersionInfo(version=pin_version)
