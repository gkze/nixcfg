"""Disable Energy's mutable desktop updater inside its packed ASAR."""

import argparse
import plistlib
import sys
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    replace_packed_file,
    write_info_plist_hash,
)

MAIN_PATH = "out/main/index.js"
_PACKAGED_GATE = b"!this.app.isPackaged"
_DISABLED_PACKAGED_GATE = b"!!1/*nix-managed*/  "
_INSTALL_ON_QUIT = b"autoUpdater.autoInstallOnAppQuit=!0"
_DISABLED_INSTALL_ON_QUIT = b"autoUpdater.autoInstallOnAppQuit=!1"


class PatchError(RuntimeError):
    """Energy's packed updater no longer matches the audited policy anchors."""


def _replace_exactly(payload: bytes, old: bytes, new: bytes, count: int) -> bytes:
    actual = payload.count(old)
    if actual != count:
        msg = f"expected {count} Energy updater policy anchors, found {actual}"
        raise PatchError(msg)
    return payload.replace(old, new)


def disable_updates(payload: bytes) -> bytes:
    """Force every packaged updater gate closed without changing file size."""
    patched = _replace_exactly(
        payload,
        _PACKAGED_GATE,
        _DISABLED_PACKAGED_GATE,
        3,
    )
    return _replace_exactly(
        patched,
        _INSTALL_ON_QUIT,
        _DISABLED_INSTALL_ON_QUIT,
        1,
    )


def patch_bundle(
    asar_path: Path,
    info_plist_path: Path,
) -> str:
    """Apply the Energy policy and refresh Electron's top-level ASAR digest."""
    digest = replace_packed_file(
        asar_path,
        MAIN_PATH,
        disable_updates,
    )
    write_info_plist_hash(info_plist_path, asar_path)
    return digest


def main(argv: list[str] | None = None) -> int:
    """Run the package-local Energy updater ownership patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar_path", type=Path)
    parser.add_argument("info_plist_path", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = patch_bundle(args.asar_path, args.info_plist_path)
    except (
        AsarIntegrityError,
        OSError,
        PatchError,
        plistlib.InvalidFileException,
    ) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"disabled Energy updates; ASAR header SHA256 {digest}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
