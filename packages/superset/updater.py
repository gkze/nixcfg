"""Updater for Superset Desktop Linux AppImage metadata."""

from typing import TYPE_CHECKING, ClassVar

from lib.bun_nix_normalizer import normalize_bun_nix
from lib.update.derivation_validation import DerivationValidation
from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.updaters import register_updater
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater
from lib.update.updaters.materialization import MaterializesArtifactsMixin

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext, VersionInfo

_BUN_ARTIFACTS = (
    "packages/superset/bun.lock",
    "packages/superset/bun.nix",
)
_BUN_ARTIFACT_DETAIL = "Superset Bun lock artifacts"


@register_updater
class SupersetUpdater(MaterializesArtifactsMixin, GitHubReleaseAssetURLsUpdater):
    """Track Superset Desktop AppImage URL and hash for Linux."""

    name = "superset"
    input_name = "superset"
    generated_artifact_files = ("bun.lock", "bun.nix")
    GITHUB_OWNER = "superset-sh"
    GITHUB_REPO = "superset"
    TAG_PREFIX = "desktop-v"
    source_pins: ClassVar[dict[str, str]] = {"electronVersion": "40.8.5"}
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=("aarch64-darwin", "x86_64-linux"),
        ),
    )

    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        return f"superset-{version}-{platform_value}.AppImage"

    def _fallback_url(self, version: str, platform_value: str) -> str:
        return (
            "https://github.com/superset-sh/superset/releases/download/"
            f"{self.TAG_PREFIX}{version}/{self._asset_name(version, platform_value)}"
        )

    def _normalize_release_version(self, tag_name: str) -> str:
        if not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected Superset release tag format: {tag_name}"
            raise RuntimeError(msg)

        version = tag_name.removeprefix(self.TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in Superset release tag: {tag_name}"
            raise RuntimeError(msg)
        return version

    def _missing_asset_message(self, expected_name: str, tag_name: str) -> str:
        return (
            "Could not find Superset desktop release asset "
            f"{expected_name!r} in tag {tag_name}"
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh checked-in Bun artifacts before hashing the AppImage."""
        context = _coerce_context(context)
        async for event in stream_command_materialized_artifacts(
            self.name,
            args=["nix", "run", ".#superset.passthru.updateScript"],
            artifact_paths=_BUN_ARTIFACTS,
            inner=super().fetch_hashes(info, session, context=context),
            dry_run=context.dry_run,
            config=self.config,
            detail=_BUN_ARTIFACT_DETAIL,
            artifact_normalizers={_BUN_ARTIFACTS[1]: normalize_bun_nix},
        ):
            yield event
