"""Disable GooeyPi's Electron self-updater in its source tree."""

import argparse
from pathlib import Path

_INDEX_PATH = Path("electron/main/index.ts")
_UPDATER_ANCHOR = (
    "const updates = new UpdateService(getAutoUpdater(), { enabled: app.isPackaged })"
)
_NIX_MANAGED_UPDATER = (
    "const updates = new UpdateService(getAutoUpdater(), { enabled: false })"
)


def patch_tree(source_root: Path) -> None:
    """Disable mutable updates while failing closed on upstream source drift."""
    index_path = source_root / _INDEX_PATH
    source = index_path.read_text(encoding="utf-8")
    matches = source.count(_UPDATER_ANCHOR)
    if matches != 1:
        msg = f"expected one GooeyPi updater anchor, found {matches}"
        raise RuntimeError(msg)
    before, _, after = source.partition(_UPDATER_ANCHOR)
    index_path.write_text(
        f"{before}{_NIX_MANAGED_UPDATER}{after}",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Apply the Nix update-ownership patch to one GooeyPi source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    args = parser.parse_args(argv)
    patch_tree(args.source_root)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
