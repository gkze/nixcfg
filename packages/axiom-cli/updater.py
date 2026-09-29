"""Updater for the axiom-cli Go vendor hash."""

from lib.update.updaters import register_updater
from lib.update.updaters.go_compatibility import GoModCompatibilityUpdater


@register_updater
class AxiomCliUpdater(GoModCompatibilityUpdater):
    """Go vendor hash updater for axiom-cli."""

    name = "axiom-cli"
    bulk_update_hold = (
        "Hosted ubuntu-24.04-arm go-modules prefetch of axiom-go@v0.37.0 "
        "failed with GOPROXY stream INTERNAL_ERROR (Update 36577228134). "
        "Keep the current pin until Linux ARM can vendor that module."
    )
    GITHUB_OWNER = "axiomhq"
    GITHUB_REPO = "cli"
