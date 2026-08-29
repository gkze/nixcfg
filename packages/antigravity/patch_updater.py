"""Disable Antigravity's mutable Electron updater inside its packed ASAR."""

import argparse
import hashlib
import plistlib
import sys
from functools import partial
from pathlib import Path

from lib.asar_integrity import (
    AsarIntegrityError,
    check_info_plist_hash,
    read_packed_file,
    replace_packed_file,
    write_info_plist_hash,
)

UPDATER_PATH = "dist/updater.js"
REVIEWED_UPDATER_SHA256 = frozenset({
    # Google Antigravity 2.9.1, aarch64-darwin.
    "3a9ccfaef9bc9a299f0e761a171997a21887dc0c7bda38bf178647ed59a60c71",
})


class PatchError(RuntimeError):
    """The vendor updater no longer matches the reviewed policy contract."""


def _same_size(original: bytes, replacement: bytes) -> bytes:
    if len(replacement) > len(original):  # pragma: no cover -- static contract
        msg = "Antigravity updater replacement exceeds its vendor anchor"
        raise AssertionError(msg)
    return replacement + b" " * (len(original) - len(replacement))


PATCHES: tuple[tuple[str, bytes, bytes, int], ...] = (
    (
        "check menu label",
        b'MenuUpdateStep["CheckForUpdates"] = "Check for Updates";',
        _same_size(
            b'MenuUpdateStep["CheckForUpdates"] = "Check for Updates";',
            b'MenuUpdateStep["CheckForUpdates"] = "Managed by Nix   ";',
        ),
        1,
    ),
    (
        "restart menu label",
        b'MenuUpdateStep["RestartToUpdate"] = "Restart to Update";',
        _same_size(
            b'MenuUpdateStep["RestartToUpdate"] = "Restart to Update";',
            b'MenuUpdateStep["RestartToUpdate"] = "Apply with Nix   ";',
        ),
        1,
    ),
    (
        "check menu action",
        b"    [MenuUpdateStep.CheckForUpdates]: () => checkForUpdates(true),",
        _same_size(
            b"    [MenuUpdateStep.CheckForUpdates]: () => checkForUpdates(true),",
            b"    [MenuUpdateStep.CheckForUpdates]: undefined,",
        ),
        1,
    ),
    (
        "restart menu action",
        b"    [MenuUpdateStep.RestartToUpdate]: () => quitAndInstall(),",
        _same_size(
            b"    [MenuUpdateStep.RestartToUpdate]: () => quitAndInstall(),",
            b"    [MenuUpdateStep.RestartToUpdate]: undefined,",
        ),
        1,
    ),
    (
        "settings toggle",
        b"    if (!updaterInitialized) {\n        return;\n    }",
        _same_size(
            b"    if (!updaterInitialized) {\n        return;\n    }",
            b"    return;",
        ),
        1,
    ),
    (
        "updater initialization",
        b"function initAutoUpdater(isHeadless, settingsService) {",
        _same_size(
            b"function initAutoUpdater(isHeadless, settingsService) {",
            b"function initAutoUpdater(_,__) { return;",
        ),
        1,
    ),
    (
        "update acquisition",
        b"function checkForUpdates(isManual = false) {",
        _same_size(
            b"function checkForUpdates(isManual = false) {",
            b"function checkForUpdates(_) { return;",
        ),
        1,
    ),
    (
        "staged update install",
        b"function quitAndInstall() {\n"
        b"    electron_updater_1.autoUpdater.quitAndInstall();\n}",
        _same_size(
            b"function quitAndInstall() {\n"
            b"    electron_updater_1.autoUpdater.quitAndInstall();\n}",
            b"function quitAndInstall() {\n    return;\n}",
        ),
        1,
    ),
    (
        "host update bridge",
        b"function applyHostUpdate() {\n    const state = getLastState();",
        _same_size(
            b"function applyHostUpdate() {\n    const state = getLastState();",
            b"function applyHostUpdate() {\n    return false;",
        ),
        1,
    ),
)


def validate_disabled_payload(payload: bytes) -> None:
    """Require every reviewed updater entry point to remain disabled."""
    for label, vendor, disabled, expected_count in PATCHES:
        if vendor in payload:
            msg = f"Antigravity {label} still contains the vendor updater anchor"
            raise PatchError(msg)
        actual_count = payload.count(disabled)
        if actual_count != expected_count:
            msg = (
                f"Antigravity disabled {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)


def disable_updates(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
) -> bytes:
    """Return the exact-size fail-closed updater transformation."""
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is None:
        if actual_sha256 not in REVIEWED_UPDATER_SHA256:
            msg = f"Antigravity updater SHA-256 is not reviewed: {actual_sha256}"
            raise PatchError(msg)
    elif actual_sha256 != expected_sha256:
        msg = (
            "Antigravity updater SHA-256 drifted: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
        raise PatchError(msg)

    patched = payload
    for label, vendor, disabled, expected_count in PATCHES:
        actual_count = patched.count(vendor)
        if actual_count != expected_count:
            msg = (
                f"Antigravity {label} inventory drifted: "
                f"expected {expected_count}, got {actual_count}"
            )
            raise PatchError(msg)
        patched = patched.replace(vendor, disabled)
    validate_disabled_payload(patched)
    return patched


def _bundle_paths(bundle: Path) -> tuple[Path, Path, Path]:
    resources = bundle / "Contents/Resources"
    return (
        resources / "app.asar",
        bundle / "Contents/Info.plist",
        resources / "app-update.yml",
    )


def patch_bundle(bundle: Path, *, expected_sha256: str | None = None) -> str:
    """Patch the packed policy, refresh integrity, and remove vendor feed config."""
    asar_path, plist_path, update_config = _bundle_paths(bundle)
    if not update_config.is_file():
        msg = f"Antigravity updater config is missing: {update_config}"
        raise PatchError(msg)
    digest = replace_packed_file(
        asar_path,
        UPDATER_PATH,
        partial(disable_updates, expected_sha256=expected_sha256),
    )
    write_info_plist_hash(plist_path, asar_path)
    update_config.unlink()
    return digest


def validate_bundle(bundle: Path) -> str:
    """Validate the patched updater, removed feed, and ASAR plist integrity."""
    asar_path, plist_path, update_config = _bundle_paths(bundle)
    if update_config.exists():
        msg = f"Antigravity vendor updater config remains: {update_config}"
        raise PatchError(msg)
    validate_disabled_payload(read_packed_file(asar_path, UPDATER_PATH))
    return check_info_plist_hash(plist_path, asar_path)


def main(argv: list[str] | None = None) -> int:
    """Run or verify the package-local Antigravity updater policy."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        digest = (
            validate_bundle(args.bundle) if args.check else patch_bundle(args.bundle)
        )
    except (
        AsarIntegrityError,
        OSError,
        PatchError,
        plistlib.InvalidFileException,
    ) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    action = "verified" if args.check else "disabled"
    sys.stdout.write(f"{action} Antigravity updates; ASAR header SHA256 {digest}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
