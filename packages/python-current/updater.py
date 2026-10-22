"""Track the newest CPython patch in the reviewed interactive series."""

import base64
import json
import re
from pathlib import Path
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

_NAME = re.compile(r"^Python (3\.[0-9]+\.[0-9]+)$")
_RESOURCE = re.compile(r"^https://www\.python\.org/api/v2/downloads/release/([0-9]+)/$")
_SERIES = json.loads(Path(__file__).with_name("compatibility.json").read_text())[
    "series"
]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def stable_release(payload: object) -> tuple[str, str]:
    """Resolve the greatest final release within the reviewed interactive series."""
    if not isinstance(payload, list):
        msg = "Python release index must be a list"
        raise TypeError(msg)
    releases = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        match = _NAME.fullmatch(name) if isinstance(name, str) else None
        if (
            match
            and item.get("is_published") is True
            and item.get("pre_release") is False
            and Version(match.group(1)).release[:2] == Version(_SERIES).release
        ):
            releases.append((Version(match.group(1)), item.get("resource_uri")))
    if not releases:
        msg = "Python release index has no stable release in the compatible series"
        raise ValueError(msg)
    latest = max(version for version, _ in releases)
    matches = [resource for version, resource in releases if version == latest]
    if len(matches) != 1 or not isinstance(matches[0], str):
        msg = "Python release index has ambiguous latest release metadata"
        raise ValueError(msg)
    resource = _RESOURCE.fullmatch(matches[0])
    if resource is None:
        msg = "Python release resource is not an official release endpoint"
        raise ValueError(msg)
    return str(latest), resource.group(1)


def source_release(version: str, payload: object) -> VersionInfo:
    """Require the unique official XZ source archive and its published digest."""
    url = f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz"
    if not isinstance(payload, list):
        msg = "Python release files must be a list"
        raise TypeError(msg)
    matches = [
        item for item in payload if isinstance(item, dict) and item.get("url") == url
    ]
    if len(matches) != 1:
        msg = "Python release must have exactly one XZ source archive"
        raise ValueError(msg)
    item = matches[0]
    digest = item.get("sha256_sum")
    if (
        item.get("is_source") is not True
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        msg = "Python source archive metadata is invalid"
        raise ValueError(msg)
    return VersionInfo(version=version, metadata={"url": url, "sha256": digest})


@register_updater
class PythonCurrentUpdater(SingleURLHashEntryUpdater):
    """Refresh only the user interpreter; independent build interpreters stay pinned."""

    name = "python-current"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve source metadata through the publisher's release API."""
        _ = context
        version, release_id = stable_release(
            await fetch_json(
                session,
                "https://www.python.org/api/v2/downloads/release/?is_published=true",
                config=self.config,
            )
        )
        files = await fetch_json(
            session,
            f"https://www.python.org/api/v2/downloads/release_file/?release={release_id}",
            config=self.config,
        )
        return source_release(version, files)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Verify the downloaded bytes against the official source digest."""
        hashes = await super().fetch_hashes(info, session, context=context, emit=emit)
        digest = require_metadata_str(
            info.metadata, "sha256", context="Python source metadata"
        )
        expected = "sha256-" + base64.b64encode(bytes.fromhex(digest)).decode()
        if (
            not isinstance(hashes, list)
            or len(hashes) != 1
            or hashes[0].hash != expected
        ):
            msg = "Python source archive does not match its publisher checksum"
            raise ValueError(msg)
        return hashes
