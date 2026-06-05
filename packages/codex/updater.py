"""Updater for codex flake input metadata and crate2nix artifacts."""

from __future__ import annotations

from dataclasses import replace

from lib.update import crate2nix as _crate2nix
from lib.update.paths import get_repo_file
from lib.update.updaters.base import Crate2NixMetadataUpdater, register_updater


def _patch_installed_crate2nix_target(name: str) -> None:
    """Keep worktree updaters compatible with older installed nixcfg CLIs."""
    if hasattr(_crate2nix, "_local_flake_installable"):
        return
    target = _crate2nix.TARGETS.get(name)
    if target is None or not target.patched_src_installable.startswith("path:.#"):
        return
    attr = target.patched_src_installable.removeprefix("path:.#")
    _crate2nix.TARGETS[name] = replace(
        target,
        patched_src_installable=f"git+file://{get_repo_file('.').resolve()}?dirty=1#{attr}",
    )


_patch_installed_crate2nix_target("codex")


@register_updater
class CodexUpdater(Crate2NixMetadataUpdater):
    """Track the codex flake input ref and locked commit in sources.json."""

    name = "codex"
