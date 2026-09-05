"""Updater for the axiom-cli Go vendor hash."""

from lib.update.updaters import register_updater
from lib.update.updaters.go_compatibility import GoModCompatibilityUpdater


@register_updater
class AxiomCliUpdater(GoModCompatibilityUpdater):
    """Go vendor hash updater for axiom-cli."""

    name = "axiom-cli"
    GITHUB_OWNER = "axiomhq"
    GITHUB_REPO = "cli"
