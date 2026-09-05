"""Updater for sentry-cli source and cargo vendor hashes."""

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
from lib.update.updaters import (
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
    RESOLVE_TAG_COMMIT = True
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"
    # Restrict hash computation to Darwin. The cargoHash
    # materializes by invoking ``rustPlatform.fetchCargoVendor``, whose
    # upstream Python helper has to download every Cargo dependency from
    # ``https://crates.io/api/v1/...``. That endpoint currently returns
    # HTTP 403 from common hosted Linux environments, while Darwin succeeds.
    # The resulting hash is platform-independent, so one Darwin refresh is
    # sufficient for the shared source entry.
    supported_platforms = ("aarch64-darwin", "x86_64-darwin")

    def _src_nix_expression(
        self,
        commit: str,
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
                    "rev": commit,
                    "hash": hash_expr,
                    "postFetch": self.XCARCHIVE_FILTER,
                },
            ),
        )

    def _src_nix_expr(self, commit: str, hash_value: str | None = None) -> str:
        return compact_nix_expr(
            self._src_nix_expression(commit, hash_value).rebuild(),
        )

    def _cargo_nix_expr(self, commit: str, src_hash: str) -> str:
        cargo_vendor_expr = FunctionCall(
            name=identifier_attr_path("pkgs", "rustPlatform", "fetchCargoVendor"),
            argument=AttributeSet.from_dict(
                {
                    "src": self._src_nix_expression(commit, src_hash),
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
        commit = self._require_commit(info)

        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: _build_nix_expr(self._src_nix_expr(commit)),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda resolved: _build_nix_expr(
                        self._cargo_nix_expr(commit, resolved["srcHash"])
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event
