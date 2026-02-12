"""Updater for sentry-cli source and cargo vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.parser import parse

if TYPE_CHECKING:
    import aiohttp

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
)
from lib.update.net import fetch_github_api
from lib.update.nix import _build_nix_expr, compute_fixed_output_hash
from lib.update.updaters.base import Updater, VersionInfo


class SentryCliUpdater(Updater):
    """Compute src/cargo hashes for the latest sentry-cli GitHub release."""

    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"

    @staticmethod
    def _compact_nix_expr(expr: str) -> str:
        return " ".join(line.strip() for line in expr.splitlines() if line.strip())

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

    def _src_nix_expression(
        self,
        version: str,
        hash_value: str | None = None,
    ) -> FunctionCall:
        hash_expr = (
            parse("pkgs.lib.fakeHash").expr if hash_value is None else hash_value
        )
        return FunctionCall(
            name="pkgs.fetchFromGitHub",
            argument=AttributeSet.from_dict(
                {
                    "owner": self.GITHUB_OWNER,
                    "repo": self.GITHUB_REPO,
                    "tag": version,
                    "hash": hash_expr,
                    "postFetch": self.XCARCHIVE_FILTER,
                },
            ),
        )

    def _src_nix_expr(self, version: str, hash_value: str | None = None) -> str:
        return self._compact_nix_expr(
            self._src_nix_expression(version, hash_value).rebuild(),
        )

    def _cargo_nix_expr(self, version: str, src_hash: str) -> str:
        cargo_vendor_expr = FunctionCall(
            name="pkgs.rustPlatform.fetchCargoVendor",
            argument=AttributeSet.from_dict(
                {
                    "src": self._src_nix_expression(version, src_hash),
                    "hash": parse("pkgs.lib.fakeHash").expr,
                },
            ),
        )
        return self._compact_nix_expr(cargo_vendor_expr.rebuild())

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
        async for item in capture_stream_value(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(self._cargo_nix_expr(info.version, src_hash)),
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
