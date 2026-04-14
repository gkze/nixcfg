"""Source-level guardrails for the Zed nightly package expression."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from lib.update.paths import REPO_ROOT


@cache
def _zed_package_text() -> str:
    """Return Zed nightly's package expression source text."""
    return Path(REPO_ROOT / "packages/zed-editor-nightly/default.nix").read_text(
        encoding="utf-8"
    )


def test_zed_fontconfig_uses_raw_source_paths() -> None:
    """Avoid forcing the Darwin-only patched source derivation during eval."""
    source = _zed_package_text()

    assert '"${src}/assets/fonts/lilex"' in source
    assert '"${src}/assets/fonts/ibm-plex-sans"' in source
    assert '"${patchedSrc}/assets/fonts/lilex"' not in source
    assert '"${patchedSrc}/assets/fonts/ibm-plex-sans"' not in source
