"""Maintain Electron ASAR header integrity entries in macOS app plists."""

from __future__ import annotations

import argparse
import hashlib
import plistlib
import struct
import sys
from pathlib import Path
from typing import Any, cast

ASAR_PREFIX_SIZE = 16
DEFAULT_ASAR_INTEGRITY_KEY = "Resources/app.asar"


class AsarIntegrityError(RuntimeError):
    """User-facing ASAR integrity failure."""


def read_asar_header(asar_path: Path) -> bytes:
    """Return the raw ASAR header bytes Electron hashes for integrity checks."""
    with asar_path.open("rb") as handle:
        prefix = handle.read(ASAR_PREFIX_SIZE)
        if len(prefix) != ASAR_PREFIX_SIZE:
            msg = f"ASAR archive is too short: {asar_path}"
            raise AsarIntegrityError(msg)
        header_size = struct.unpack("<I", prefix[12:16])[0]
        header = handle.read(header_size)

    if len(header) != header_size:
        msg = (
            f"ASAR archive header is truncated: expected {header_size} bytes, "
            f"read {len(header)}"
        )
        raise AsarIntegrityError(msg)
    return header


def asar_header_hash(asar_path: Path) -> str:
    """Return the SHA256 hex digest Electron expects in ``ElectronAsarIntegrity``."""
    return hashlib.sha256(read_asar_header(asar_path)).hexdigest()


def _load_plist_dict(plist_path: Path) -> dict[str, Any]:
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    if not isinstance(payload, dict):
        msg = f"Expected a plist dictionary in {plist_path}"
        raise AsarIntegrityError(msg)
    return cast("dict[str, Any]", payload)


def write_info_plist_hash(
    plist_path: Path,
    asar_path: Path,
    *,
    key: str = DEFAULT_ASAR_INTEGRITY_KEY,
) -> str:
    """Write the ASAR header hash for *asar_path* into *plist_path*."""
    digest = asar_header_hash(asar_path)
    info = _load_plist_dict(plist_path)
    integrity = info.setdefault("ElectronAsarIntegrity", {})
    if not isinstance(integrity, dict):
        msg = f"Expected ElectronAsarIntegrity dictionary in {plist_path}"
        raise AsarIntegrityError(msg)
    integrity[key] = {
        "algorithm": "SHA256",
        "hash": digest,
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle)
    return digest


def check_info_plist_hash(
    plist_path: Path,
    asar_path: Path,
    *,
    key: str = DEFAULT_ASAR_INTEGRITY_KEY,
) -> str:
    """Raise if *plist_path* does not contain *asar_path*'s ASAR header hash."""
    info = _load_plist_dict(plist_path)
    try:
        integrity = info["ElectronAsarIntegrity"]
        entry = integrity[key]
    except (KeyError, TypeError) as exc:
        msg = f"Missing ElectronAsarIntegrity entry {key!r} in {plist_path}"
        raise AsarIntegrityError(msg) from exc

    if not isinstance(entry, dict):
        msg = f"ElectronAsarIntegrity entry {key!r} must be a dictionary"
        raise AsarIntegrityError(msg)

    algorithm = entry.get("algorithm")
    if algorithm != "SHA256":
        msg = f"ElectronAsarIntegrity entry {key!r} must use SHA256, got {algorithm!r}"
        raise AsarIntegrityError(msg)

    expected = entry.get("hash")
    if not isinstance(expected, str):
        msg = f"ElectronAsarIntegrity entry {key!r} has a non-string hash"
        raise AsarIntegrityError(msg)

    actual = asar_header_hash(asar_path)
    if actual != expected:
        msg = (
            f"ASAR ElectronAsarIntegrity mismatch for {asar_path}: "
            f"expected {expected}, got {actual}"
        )
        raise AsarIntegrityError(msg)
    return actual


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("set-info-plist-hash", "check-info-plist-hash"):
        command = subparsers.add_parser(name)
        command.add_argument("plist_path", type=Path)
        command.add_argument("asar_path", type=Path)
        command.add_argument("--key", default=DEFAULT_ASAR_INTEGRITY_KEY)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the ASAR integrity CLI."""
    args = _parse_args(argv)
    try:
        if args.command == "set-info-plist-hash":
            digest = write_info_plist_hash(
                args.plist_path, args.asar_path, key=args.key
            )
            sys.stdout.write(f"updated {args.plist_path} {args.key} to {digest}\n")
            return 0
        if args.command == "check-info-plist-hash":
            digest = check_info_plist_hash(
                args.plist_path, args.asar_path, key=args.key
            )
            sys.stdout.write(f"verified {args.plist_path} {args.key} as {digest}\n")
            return 0
    except (AsarIntegrityError, OSError, plistlib.InvalidFileException) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    sys.stderr.write(f"unknown command: {args.command}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
