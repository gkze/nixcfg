"""Updater for the Rio source package."""

from typing import TYPE_CHECKING

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_url, github_raw_url
from lib.update.rust_toolchain import rust_toolchain_channel
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    register_updater,
)
from lib.update.updaters.metadata import VersionInfo, require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters import UpdateContext

_RUST_TOOLCHAIN_FILE = "rust-toolchain.toml"


@register_updater
class RioUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Track Rio releases and compute source plus Cargo vendor hashes."""

    name = "rio"
    GITHUB_OWNER = "raphamorim"
    GITHUB_REPO = "rio"
    dependency_hash_type = "cargoHash"
    supported_platforms = ("aarch64-darwin",)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
            systems=("aarch64-darwin",),
        ),
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the release commit and the Rust release that commit pins."""
        info = await super().fetch_latest(session, context=context)
        commit = self._source_commit(info)
        manifest = await fetch_url(
            session,
            github_raw_url(
                self.GITHUB_OWNER, self.GITHUB_REPO, commit, _RUST_TOOLCHAIN_FILE
            ),
            config=self.config,
        )
        channel = rust_toolchain_channel(
            manifest, context=f"Rio {info.version} {_RUST_TOOLCHAIN_FILE}"
        )
        return VersionInfo(
            version=info.version,
            metadata={
                "commit": commit,
                "tag": require_metadata_str(
                    info.metadata, "tag", context="Rio release metadata"
                ),
                "rustToolchain": channel,
            },
        )

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the exact Rust release upstream builds this commit with."""
        return {
            "rustToolchain": require_metadata_str(
                info.metadata, "rustToolchain", context="Rio release metadata"
            )
        }
