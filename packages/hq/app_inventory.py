"""The reviewed HQ release archive's app-bundle layout.

HQ 0.11.39 keeps the menu-bar app at the archive root and nests the
capture helper inside it. Both paths are the inventory. Any other bundle,
or a missing helper, is a different archive and must fail the install.
"""

import sys
from pathlib import Path

HQ_APP = "HQ.app"
HQ_CAPTURE_HELPER = "HQ.app/Contents/Helpers/HQ Capture.app"
EXPECTED_APPS = (HQ_APP, HQ_CAPTURE_HELPER)


def app_inventory(root: Path) -> list[str]:
    """Return every `.app` path under an unpacked release, including nested ones."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.app"))


def require_app_inventory(root: Path) -> None:
    """Reject a release whose bundles are not the root app and its helper."""
    apps = app_inventory(root)
    if apps != list(EXPECTED_APPS):
        message = f"unexpected HQ app inventory: {apps!r}"
        raise SystemExit(message)


def main(argv: list[str]) -> None:
    """Check the unpacked archive named by the install phase."""
    try:
        [_, unpack_root] = argv
    except ValueError:
        message = "usage: app_inventory.py UNPACK_ROOT"
        raise SystemExit(message) from None
    require_app_inventory(Path(unpack_root))


if __name__ == "__main__":
    main(sys.argv)  # pragma: no cover -- script entry
