"""Updater for codex flake input metadata and crate2nix artifacts."""

from __future__ import annotations

from lib.update import crate2nix as _crate2nix
from lib.update.crate2nix_compat import patch_installed_crate2nix_target
from lib.update.updaters import Crate2NixMetadataUpdater, register_updater

patch_installed_crate2nix_target(_crate2nix, "codex")


@register_updater
class CodexUpdater(Crate2NixMetadataUpdater):
    """Track the codex flake input ref and locked commit in sources.json."""

    name = "codex"
