"""Updater for sentry-cli source and cargo vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.set import AttributeSet

if TYPE_CHECKING:
    import aiohttp
    from nix_manipulator.expressions.expression import NixExpression

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

from lib.update.nix import _build_nix_expr
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.updaters.base import (
    FixedOutputHashStep,
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater


@register_updater
class SentryCliUpdater(GitHubReleaseUpdater):
    """Compute src/cargo hashes for the latest sentry-cli GitHub release."""

    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    TAG_PREFIX = ""
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"
    # Restrict the hash computation to darwin runners. The cargoHash
    # materializes by invoking ``rustPlatform.fetchCargoVendor``, whose
    # upstream Python helper has to download every Cargo dependency from
    # ``https://crates.io/api/v1/...``. That endpoint currently returns
    # HTTP 403 for the GitHub Actions Linux runner IP pool (reproduced on
    # both ubuntu-24.04 and ubuntu-24.04-arm), but succeeds from macos-15.
    # The resulting hash is platform-independent, so computing it only on
    # darwin and letting merge-sources propagate the entry is sufficient.
    supported_platforms = ("aarch64-darwin", "x86_64-darwin")

    def _src_nix_expression(
        self,
        version: str,
        hash_value: str | None = None,
    ) -> FunctionCall:
        hash_expr: str | NixExpression = (
            identifier_attr_path("pkgs", "lib", "fakeHash")
            if hash_value is None
            else hash_value
        )
        return FunctionCall(
            name=identifier_attr_path("pkgs", "fetchFromGitHub"),
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
        return compact_nix_expr(
            self._src_nix_expression(version, hash_value).rebuild(),
        )

    def _cargo_nix_expr(self, version: str, src_hash: str) -> str:
        cargo_vendor_expr = FunctionCall(
            name=identifier_attr_path("pkgs", "rustPlatform", "fetchCargoVendor"),
            argument=AttributeSet.from_dict(
                {
                    "src": self._src_nix_expression(version, src_hash),
                    "hash": identifier_attr_path("pkgs", "lib", "fakeHash"),
                },
            ),
        )
        return compact_nix_expr(cargo_vendor_expr.rebuild())

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute ``srcHash`` and ``cargoHash`` via fixed-output builds."""
        _ = (session, context)

        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: _build_nix_expr(
                        self._src_nix_expr(info.version)
                    ),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda resolved: _build_nix_expr(
                        self._cargo_nix_expr(info.version, resolved["srcHash"])
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event
