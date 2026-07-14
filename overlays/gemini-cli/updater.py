"""Updater for gemini-cli source and npm dependency hashes."""

from __future__ import annotations

from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    SourceThenOverlayHashMixin,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater


@register_updater
class GeminiCliUpdater(SourceThenOverlayHashMixin, GitHubReleaseUpdater):
    """Resolve latest gemini-cli tag and compute src/npm fixed-output hashes."""

    name = "gemini-cli"
    GITHUB_OWNER = "google-gemini"
    GITHUB_REPO = "gemini-cli"
    dependency_hash_type = "npmDepsHash"

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "google-gemini",
            "gemini-cli",
            tag=f"v{version}",
        )
