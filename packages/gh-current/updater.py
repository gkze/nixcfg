"""Track GitHub CLI source releases with the maintained native Go recipe."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_github_api
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import metadata_as_mapping, require_metadata_str

if TYPE_CHECKING:
    import aiohttp


def _commit_source_epoch(payload: object, commit: str) -> str:
    """Use the exact source commit's UTC committer time for reproducible builds."""
    if not isinstance(payload, dict) or payload.get("sha") != commit:
        msg = "GitHub CLI commit timestamp response has the wrong SHA"
        raise ValueError(msg)
    details = payload.get("commit")
    committer = details.get("committer") if isinstance(details, dict) else None
    date = committer.get("date") if isinstance(committer, dict) else None
    if not isinstance(date, str):
        msg = "GitHub CLI commit timestamp is missing"
        raise TypeError(msg)
    try:
        timestamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        msg = "GitHub CLI commit timestamp must be UTC seconds"
        raise ValueError(msg) from error
    epoch = int(timestamp.timestamp())
    if epoch <= 0:
        msg = "GitHub CLI commit timestamp must be positive"
        raise ValueError(msg)
    return str(epoch)


@register_updater
class GhCurrentUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Refresh immutable source and vendor hashes together."""

    name = "gh-current"
    GITHUB_OWNER = "cli"
    GITHUB_REPO = "cli"
    dependency_hash_type = "vendorHash"
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
        """Resolve the release and its exact source commit's reproducible build time."""
        info = await super().fetch_latest(session, context=context)
        commit = self._source_commit(info)
        payload = await fetch_github_api(
            session, f"repos/cli/cli/commits/{commit}", config=self.config
        )
        return VersionInfo(
            version=info.version,
            metadata={
                **metadata_as_mapping(info.metadata, context="GitHub CLI release"),
                "sourceDateEpoch": _commit_source_epoch(payload, commit),
            },
        )

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Carry the validated commit timestamp through hashing and publication."""
        epoch = require_metadata_str(
            info.metadata, "sourceDateEpoch", context="GitHub CLI release"
        )
        if not epoch.isascii() or not epoch.isdecimal() or epoch.startswith("0"):
            msg = "GitHub CLI sourceDateEpoch must be canonical positive seconds"
            raise ValueError(msg)
        return {**(super().source_pins_for(info) or {}), "sourceDateEpoch": epoch}

    @staticmethod
    def _src_expr(commit: str) -> str:
        return _build_fetch_from_github_expr(
            "cli", "cli", rev=commit, fetch_submodules=False
        )
