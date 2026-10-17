"""Disable GooeyPi's Electron self-updater in its source tree."""

from typing import TYPE_CHECKING

from lib.codemods.source_patch import SourcePatch, apply_patches, run_source_tree_cli

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_INDEX_PATH = "electron/main/index.ts"
_UPDATER_ANCHOR = (
    "const updates = new UpdateService(getAutoUpdater(), { enabled: app.isPackaged })"
)
_NIX_MANAGED_UPDATER = (
    "const updates = new UpdateService(getAutoUpdater(), { enabled: false })"
)
_PATCHES = (SourcePatch(_INDEX_PATH, _UPDATER_ANCHOR, _NIX_MANAGED_UPDATER),)


def patch_tree(source_root: Path) -> None:
    """Disable mutable updates while failing closed on upstream source drift."""
    apply_patches(source_root, _PATCHES)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply the Nix update-ownership patch to one GooeyPi source tree."""
    return run_source_tree_cli(argv, description=__doc__, patch_tree=patch_tree)


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
