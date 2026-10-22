"""Track the latest stable Go source release with its publisher checksum."""

import base64
import re
from typing import TYPE_CHECKING

from packaging.version import Version

from lib.update.events import ignore_event
from lib.update.net import fetch_json
from lib.update.updaters import SingleURLHashEntryUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceHashes
    from lib.update.events import EventSink
    from lib.update.updaters import UpdateContext

_VERSION = re.compile(r"^go(1\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def stable_source_release(payload: object) -> VersionInfo:
    """Select the greatest stable release and exactly one matching source asset."""
    if not isinstance(payload, list):
        msg = "Go release index must be a list"
        raise TypeError(msg)
    stable = []
    for release in payload:
        if not isinstance(release, dict) or release.get("stable") is not True:
            continue
        version = release.get("version")
        match = _VERSION.fullmatch(version) if isinstance(version, str) else None
        if match is None:
            msg = "Go stable release has an invalid version"
            raise ValueError(msg)
        stable.append((Version(match.group(1)), release))
    if not stable:
        msg = "Go release index has no stable release"
        raise ValueError(msg)
    selected_version = max(version for version, _ in stable)
    candidates = [release for version, release in stable if version == selected_version]
    if len(candidates) != 1:
        msg = "Go release index has duplicate latest stable releases"
        raise ValueError(msg)
    release = candidates[0]
    version = str(selected_version)
    filename = f"go{version}.src.tar.gz"
    files = release.get("files")
    if not isinstance(files, list):
        msg = "Go release files must be a list"
        raise TypeError(msg)
    assets = [
        asset
        for asset in files
        if isinstance(asset, dict) and asset.get("filename") == filename
    ]
    if len(assets) != 1:
        msg = "Go release must contain exactly one matching source archive"
        raise ValueError(msg)
    asset = assets[0]
    checksum = asset.get("sha256")
    if (
        asset.get("version") != f"go{version}"
        or asset.get("kind") != "source"
        or asset.get("os") != ""
        or asset.get("arch") != ""
        or not isinstance(checksum, str)
        or _SHA256.fullmatch(checksum) is None
        or not isinstance(asset.get("size"), int)
        or asset["size"] <= 0
    ):
        msg = "Go source archive metadata is invalid"
        raise ValueError(msg)
    return VersionInfo(
        version=version,
        metadata={"url": f"https://go.dev/dl/{filename}", "sha256": checksum},
    )


@register_updater
class GoToolchainUpdater(SingleURLHashEntryUpdater):
    """Use the maintained Nix Go recipe with the stable upstream source archive."""

    name = "go-toolchain"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the latest stable source entry from the official release index."""
        _ = context
        payload = await fetch_json(
            session, "https://go.dev/dl/?mode=json", config=self.config
        )
        return stable_source_release(payload)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Require the downloaded archive to match the published digest."""
        hashes = await super().fetch_hashes(info, session, context=context, emit=emit)
        expected = (
            "sha256-"
            + base64.b64encode(
                bytes.fromhex(
                    require_metadata_str(
                        info.metadata, "sha256", context="Go release metadata"
                    )
                )
            ).decode()
        )
        if (
            not isinstance(hashes, list)
            or len(hashes) != 1
            or hashes[0].hash != expected
        ):
            msg = "Go source archive does not match its publisher checksum"
            raise ValueError(msg)
        return hashes
