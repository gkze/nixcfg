"""Updater for codex flake input metadata and crate2nix artifacts."""

from lib.update.updaters.base import FlakeInputMetadataUpdater, register_updater


@register_updater
class CodexUpdater(FlakeInputMetadataUpdater):
    """Track the codex flake input ref and locked commit in sources.json."""

    name = "codex"
