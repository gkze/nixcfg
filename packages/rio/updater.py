"""Updater for the Rio source package."""

from __future__ import annotations

from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    register_updater,
)


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
        ),
    )

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "raphamorim",
            "rio",
            tag=f"v{version}",
            fetch_submodules=False,
        )
