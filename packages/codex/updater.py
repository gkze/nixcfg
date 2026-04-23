"""Updater for codex flake input metadata and crate2nix artifacts."""

from lib.update.updaters.base import Crate2NixMetadataUpdater, register_updater


@register_updater
class CodexUpdater(Crate2NixMetadataUpdater):
    """Track the codex flake input ref and locked commit in sources.json."""

    name = "codex"
