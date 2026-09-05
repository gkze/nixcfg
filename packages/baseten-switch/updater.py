"""Updater for the source-built Baseten Switch macOS app and CLI."""

from typing import TYPE_CHECKING

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_github_api_paginated
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    SourceThenOverlayHashMixin,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class BasetenSwitchUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Track Baseten Switch beta releases and source-build dependency hashes."""

    name = "baseten-switch"
    GITHUB_OWNER = "basetenlabs"
    GITHUB_REPO = "baseten-switch"
    dependency_hash_type = "vendorHash"
    supported_platforms = ("aarch64-darwin", "x86_64-darwin")
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Select the newest published release, including the public beta line."""
        releases = await fetch_github_api_paginated(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases",
            config=self.config,
            per_page=100,
        )
        for release in releases:
            if not isinstance(release, dict):
                msg = f"Unexpected release payload type: {type(release).__name__}"
                raise TypeError(msg)
            if release.get("draft") is True:
                continue
            tag_name = release.get("tag_name")
            if not isinstance(tag_name, str) or not tag_name:
                msg = f"Missing tag_name in release payload: {release!r}"
                raise RuntimeError(msg)
            commit = await self._resolve_release_tag_commit(session, tag_name)
            return VersionInfo(
                version=self._normalize_release_version(tag_name),
                metadata={"commit": commit, "tag": tag_name},
            )
        msg = f"No published releases found for {self.GITHUB_OWNER}/{self.GITHUB_REPO}"
        raise RuntimeError(msg)

    @staticmethod
    def _src_expr(commit: str) -> str:
        return _build_fetch_from_github_expr(
            "basetenlabs",
            "baseten-switch",
            rev=commit,
            fetch_submodules=False,
        )
