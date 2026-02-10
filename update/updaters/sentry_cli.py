"""Updater for sentry-cli source and cargo vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import aiohttp

from libnix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
)
from update.net import fetch_github_api
from update.nix import _build_nix_expr, compute_fixed_output_hash
from update.updaters.base import Updater, VersionInfo


class SentryCliUpdater(Updater):
    """Compute src/cargo hashes for the latest sentry-cli GitHub release."""

    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch latest release tag from GitHub."""
        data = cast(
            "dict[str, str]",
            await fetch_github_api(
                session,
                f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
                config=self.config,
            ),
        )
        return VersionInfo(version=data["tag_name"], metadata={})

    def _src_nix_expr(self, version: str, hash_value: str = "pkgs.lib.fakeHash") -> str:
        return (
            f"pkgs.fetchFromGitHub {{\n"
            f'  owner = "{self.GITHUB_OWNER}";\n'
            f'  repo = "{self.GITHUB_REPO}";\n'
            f'  tag = "{version}";\n'
            f"  hash = {hash_value};\n"
            f'  postFetch = "{self.XCARCHIVE_FILTER}";\n'
            f"}}"
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute ``srcHash`` and ``cargoHash`` via fixed-output builds."""
        _ = session
        src_hash: str | None = None
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(self._src_nix_expr(info.version)),
            ),
            error="Missing srcHash output",
        ):
            if isinstance(item, CapturedValue):
                src_hash = cast("str", item.captured)
            else:
                yield item
        if src_hash is None:
            msg = "Missing srcHash output"
            raise RuntimeError(msg)

        cargo_hash: str | None = None
        src_expr = self._src_nix_expr(info.version, f'"{src_hash}"')
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(
                    f"pkgs.rustPlatform.fetchCargoVendor {{\n"
                    f"  src = {src_expr};\n"
                    f"  hash = pkgs.lib.fakeHash;\n"
                    f"}}",
                ),
            ),
            error="Missing cargoHash output",
        ):
            if isinstance(item, CapturedValue):
                cargo_hash = cast("str", item.captured)
            else:
                yield item
        if cargo_hash is None:
            msg = "Missing cargoHash output"
            raise RuntimeError(msg)

        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("cargoHash", cargo_hash),
            ],
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build source entry for sentry-cli."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
        )
