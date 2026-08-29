"""Disable Zo's mutable Electron updater inside its ASAR."""

import argparse
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    read_packed_file,
    replace_packed_file_preserving_header,
    write_info_plist_hash,
)

MAIN_PATH = "out/main/index.js"
SENTRY_SDK_PATH = "node_modules/@sentry/electron/main/sdk.js"

_UPDATER_FUNCTION = b"""function getAutoUpdater() {
  const { autoUpdater } = electronUpdater;
  return autoUpdater;
}"""
_DISABLED_UPDATER_FUNCTION = b"""function getAutoUpdater() {
  return new Proxy({}, { get: () => () => Promise.resolve() });
}"""
_SENTRY_MINIDUMP_DEFAULT = (
    b"        index.sentryMinidumpIntegration(), // we want this to run first as it "
    b"enables the native crash handler"
)
_SENTRY_WITHOUT_MINIDUMPS = (
    b"        // Nix owns native crash handling; keep Sentry JavaScript telemetry only."
)


class PatchError(RuntimeError):
    """The vendor archive does not match the expected Zo contract."""


def _replace_padded_once(
    payload: bytes,
    old: bytes,
    new: bytes,
    *,
    name: str,
) -> bytes:
    if len(new) > len(old):
        msg = f"Zo {name} replacement is longer than its stable ASAR anchor"
        raise PatchError(msg)
    count = payload.count(old)
    if count != 1:
        msg = f"expected one Zo {name} anchor, found {count}"
        raise PatchError(msg)
    offset = payload.find(old)
    replacement = new + b" " * (len(old) - len(new))
    return payload[:offset] + replacement + payload[offset + len(old) :]


def _patch_main_payload(original: bytes) -> bytes:
    return _replace_padded_once(
        original,
        _UPDATER_FUNCTION,
        _DISABLED_UPDATER_FUNCTION,
        name="updater",
    )


def _patch_sentry_payload(original: bytes) -> bytes:
    return _replace_padded_once(
        original,
        _SENTRY_MINIDUMP_DEFAULT,
        _SENTRY_WITHOUT_MINIDUMPS,
        name="Sentry minidump integration",
    )


def _patch_asar(asar_path: Path) -> str:
    try:
        # Validate both vendor contracts before changing either packed file.
        _patch_main_payload(read_packed_file(asar_path, MAIN_PATH))
        _patch_sentry_payload(read_packed_file(asar_path, SENTRY_SDK_PATH))
        replace_packed_file_preserving_header(
            asar_path,
            MAIN_PATH,
            _patch_main_payload,
        )
        return replace_packed_file_preserving_header(
            asar_path,
            SENTRY_SDK_PATH,
            _patch_sentry_payload,
        )
    except AsarIntegrityError as exc:
        msg = f"Zo {exc}"
        raise PatchError(msg) from exc


def patch_bundle(asar_path: Path, info_plist_path: Path) -> str:
    """Stage both integrity rewrites before atomically publishing either file."""
    with (
        tempfile.TemporaryDirectory(
            dir=asar_path.parent,
            prefix=f".{asar_path.name}.",
        ) as asar_staging_dir,
        tempfile.TemporaryDirectory(
            dir=info_plist_path.parent,
            prefix=f".{info_plist_path.name}.",
        ) as plist_staging_dir,
    ):
        staged_asar = Path(asar_staging_dir) / asar_path.name
        original_asar = Path(asar_staging_dir) / f"{asar_path.name}.original"
        staged_plist = Path(plist_staging_dir) / info_plist_path.name
        shutil.copy2(asar_path, staged_asar)
        shutil.copy2(asar_path, original_asar)
        shutil.copy2(info_plist_path, staged_plist)

        _patch_asar(staged_asar)
        try:
            digest = write_info_plist_hash(staged_plist, staged_asar)
        except AsarIntegrityError as exc:
            msg = f"Zo {exc}"
            raise PatchError(msg) from exc

        staged_asar.replace(asar_path)
        try:
            staged_plist.replace(info_plist_path)
        except OSError:
            original_asar.replace(asar_path)
            raise
        return digest


def main(argv: list[str] | None = None) -> int:
    """Run the package-local Zo updater ownership patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asar_path", type=Path)
    parser.add_argument("info_plist_path", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = patch_bundle(args.asar_path, args.info_plist_path)
    except (OSError, PatchError, plistlib.InvalidFileException) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"disabled Zo updates; ASAR header SHA256 {digest}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
