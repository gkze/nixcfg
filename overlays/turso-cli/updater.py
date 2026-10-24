"""Updater for turso-cli source and Go vendor hashes."""

from lib.update.derivation_validation import DerivationValidation
from lib.update.updaters import (
    GitHubReleaseUpdater,
    SourceThenOverlayHashMixin,
    register_updater,
)


@register_updater
class TursoCliUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Track turso-cli releases and refresh the source-build hashes."""

    name = "turso-cli"
    GITHUB_OWNER = "tursodatabase"
    GITHUB_REPO = "turso-cli"
    dependency_hash_type = "vendorHash"
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )
