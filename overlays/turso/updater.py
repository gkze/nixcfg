"""Updater for Turso source and Cargo vendor hashes."""

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    register_updater,
)


@register_updater
class TursoUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Track Turso releases and refresh the source-build hashes."""

    name = "turso"
    GITHUB_OWNER = "tursodatabase"
    GITHUB_REPO = "turso"
    dependency_hash_type = "cargoHash"
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )
