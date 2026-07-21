"""Updater for tsgolint source and vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry

from lib.nix.models.sources import HashCollection
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    SourceThenOverlayHashMixin,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater


@register_updater
class TsgolintUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Resolve tsgolint releases and refresh the checked-in Nix source hashes."""

    name = "tsgolint"
    GITHUB_OWNER = "oxc-project"
    GITHUB_REPO = "tsgolint"
    dependency_hash_type = "vendorHash"
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "oxc-project",
            "tsgolint",
            tag=f"v{version}",
            fetch_submodules=False,
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Treat fake source hashes as stale so placeholder entries rehash once."""
        if isinstance(context, UpdateContext):
            update_context = context
        else:
            update_context = UpdateContext(current=context)
        current = update_context.current
        if current is None or current.version != info.version:
            return False

        hashes = current.hashes
        entries = hashes.entries
        if entries is not None:
            if not entries:
                return False
            return not any(
                entry.hash.startswith(HashCollection.FAKE_HASH_PREFIX)
                for entry in entries
            )

        mapping = hashes.mapping
        if mapping is not None:
            if not mapping:
                return False
            return not any(
                hash_value.startswith(HashCollection.FAKE_HASH_PREFIX)
                for hash_value in mapping.values()
            )

        return False
