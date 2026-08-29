"""Updater for the source-built Baseten CLI."""

from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    register_updater,
)


@register_updater
class BasetenUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Track stable Baseten CLI releases and source-build dependency hashes."""

    name = "baseten"
    GITHUB_OWNER = "basetenlabs"
    GITHUB_REPO = "baseten-cli"
    dependency_hash_type = "vendorHash"
    supported_platforms = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "basetenlabs",
            "baseten-cli",
            tag=f"v{version}",
            fetch_submodules=False,
        )
