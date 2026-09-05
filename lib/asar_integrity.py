"""Maintain Electron ASAR header integrity entries in macOS app plists."""

import argparse
import hashlib
import json
import plistlib
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO

ASAR_PREFIX_SIZE = 16
DEFAULT_ASAR_INTEGRITY_KEY = "Resources/app.asar"


class AsarIntegrityError(RuntimeError):
    """User-facing ASAR integrity failure."""


def _read_asar_layout(
    handle: BinaryIO,
) -> tuple[bytes, dict[str, Any], int]:
    prefix = handle.read(ASAR_PREFIX_SIZE)
    if len(prefix) != ASAR_PREFIX_SIZE:
        msg = "ASAR archive is too short"
        raise AsarIntegrityError(msg)
    archive_header_size = struct.unpack("<I", prefix[4:8])[0]
    header_size = struct.unpack("<I", prefix[12:16])[0]
    header_bytes = handle.read(header_size)
    if len(header_bytes) != header_size:
        msg = (
            f"ASAR archive header is truncated: expected {header_size} bytes, "
            f"read {len(header_bytes)}"
        )
        raise AsarIntegrityError(msg)
    try:
        loaded = json.loads(header_bytes.rstrip(b"\0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "ASAR archive header is not valid JSON"
        raise AsarIntegrityError(msg) from exc
    if not isinstance(loaded, dict):
        msg = "ASAR archive header is not an object"
        raise AsarIntegrityError(msg)
    data_offset = 8 + archive_header_size
    if data_offset < ASAR_PREFIX_SIZE + header_size:
        msg = "ASAR archive data overlaps its header"
        raise AsarIntegrityError(msg)
    return header_bytes, cast("dict[str, Any]", loaded), data_offset


def _packed_file_entry(header: dict[str, Any], relative_path: str) -> dict[str, Any]:
    node = header
    for component in relative_path.split("/"):
        files = node.get("files")
        child = files.get(component) if isinstance(files, dict) else None
        if not isinstance(child, dict):
            msg = f"ASAR archive is missing packed file {relative_path!r}"
            raise AsarIntegrityError(msg)
        node = cast("dict[str, Any]", child)
    if node.get("unpacked"):
        msg = f"ASAR file {relative_path!r} is stored outside the archive"
        raise AsarIntegrityError(msg)
    return node


def _packed_file_metadata(
    entry: dict[str, Any], relative_path: str
) -> tuple[int, int, dict[str, Any], str, int, list[str]]:
    try:
        size = int(entry["size"])
        offset = int(entry["offset"])
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"ASAR file {relative_path!r} has an invalid size or offset"
        raise AsarIntegrityError(msg) from exc
    if size < 0 or offset < 0:
        msg = f"ASAR file {relative_path!r} has an invalid size or offset"
        raise AsarIntegrityError(msg)
    integrity = entry.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("algorithm") != "SHA256":
        msg = f"ASAR file {relative_path!r} lacks SHA256 integrity metadata"
        raise AsarIntegrityError(msg)
    digest = integrity.get("hash")
    block_size = integrity.get("blockSize")
    blocks = integrity.get("blocks")
    if (
        not isinstance(digest, str)
        or not isinstance(block_size, int)
        or block_size <= 0
        or not isinstance(blocks, list)
        or not all(isinstance(value, str) for value in blocks)
    ):
        msg = f"ASAR file {relative_path!r} has malformed integrity metadata"
        raise AsarIntegrityError(msg)
    return size, offset, integrity, digest, block_size, cast("list[str]", blocks)


def _block_digests(payload: bytes, block_size: int) -> list[str]:
    return [
        hashlib.sha256(payload[offset : offset + block_size]).hexdigest()
        for offset in range(0, len(payload), block_size)
    ]


def _rewrite_header_digests(
    header_bytes: bytes,
    *,
    old_digests: list[str],
    new_digests: list[str],
) -> bytes:
    replacements: dict[bytes, bytes] = {}
    expected_counts: Counter[bytes] = Counter()
    for old, new in zip(old_digests, new_digests, strict=True):
        old_bytes = old.encode("ascii")
        new_bytes = new.encode("ascii")
        existing = replacements.setdefault(old_bytes, new_bytes)
        if existing != new_bytes:
            msg = "ASAR reused one old integrity digest for different replacement data"
            raise AsarIntegrityError(msg)
        expected_counts[old_bytes] += 1

    updated = bytearray(header_bytes)
    for old, new in replacements.items():
        actual_count = updated.count(old)
        expected_count = expected_counts[old]
        if actual_count != expected_count:
            msg = (
                f"expected {expected_count} ASAR integrity entries for {old.decode()}, "
                f"found {actual_count}"
            )
            raise AsarIntegrityError(msg)
        start = 0
        for _ in range(expected_count):
            offset = updated.find(old, start)
            updated[offset : offset + len(old)] = new
            start = offset + len(new)
    return bytes(updated)


def _read_verified_packed_file(
    handle: BinaryIO,
    *,
    data_offset: int,
    size: int,
    offset: int,
    digest: str,
    block_size: int,
    blocks: list[str],
    relative_path: str,
) -> bytes:
    handle.seek(data_offset + offset)
    payload = handle.read(size)
    if len(payload) != size:
        msg = f"ASAR file {relative_path!r} is truncated"
        raise AsarIntegrityError(msg)
    if hashlib.sha256(payload).hexdigest() != digest:
        msg = f"ASAR file {relative_path!r} does not match its integrity hash"
        raise AsarIntegrityError(msg)
    if _block_digests(payload, block_size) != blocks:
        msg = f"ASAR file {relative_path!r} does not match its block hashes"
        raise AsarIntegrityError(msg)
    return payload


def read_packed_file(asar_path: Path, relative_path: str) -> bytes:
    """Read one packed ASAR file after validating its SHA256 metadata."""
    with asar_path.open("rb") as handle:
        _header_bytes, header, data_offset = _read_asar_layout(handle)
        entry = _packed_file_entry(header, relative_path)
        size, offset, _integrity, digest, block_size, blocks = _packed_file_metadata(
            entry, relative_path
        )
        return _read_verified_packed_file(
            handle,
            data_offset=data_offset,
            size=size,
            offset=offset,
            digest=digest,
            block_size=block_size,
            blocks=blocks,
            relative_path=relative_path,
        )


def packed_file_paths(asar_path: Path) -> tuple[str, ...]:
    """Return the sorted relative paths of files stored inside an ASAR."""
    with asar_path.open("rb") as handle:
        _header_bytes, header, _data_offset = _read_asar_layout(handle)

    paths: list[str] = []

    def collect(node: dict[str, Any], prefix: tuple[str, ...]) -> None:
        files = node.get("files")
        if not isinstance(files, dict):
            msg = f"ASAR directory {'/'.join(prefix)!r} has malformed file inventory"
            raise AsarIntegrityError(msg)
        for name, raw_entry in files.items():
            if not isinstance(raw_entry, dict):
                msg = f"ASAR entry {'/'.join((*prefix, name))!r} is not an object"
                raise AsarIntegrityError(msg)
            components = (*prefix, name)
            if "files" in raw_entry:
                collect(cast("dict[str, Any]", raw_entry), components)
            elif not raw_entry.get("unpacked") and "link" not in raw_entry:
                paths.append("/".join(components))

    collect(header, ())
    return tuple(sorted(paths))


def _replace_packed_file(
    asar_path: Path,
    relative_path: str,
    transform: Callable[[bytes], bytes],
    *,
    preserve_header_bytes: bool,
) -> str:
    with asar_path.open("r+b") as handle:
        header_bytes, header, data_offset = _read_asar_layout(handle)
        entry = _packed_file_entry(header, relative_path)
        size, offset, integrity, digest, block_size, blocks = _packed_file_metadata(
            entry, relative_path
        )
        original = _read_verified_packed_file(
            handle,
            data_offset=data_offset,
            size=size,
            offset=offset,
            digest=digest,
            block_size=block_size,
            blocks=blocks,
            relative_path=relative_path,
        )
        replacement = transform(original)
        if not isinstance(replacement, bytes):
            msg = "ASAR packed-file transform must return bytes"
            raise AsarIntegrityError(msg)
        if len(replacement) != len(original):
            msg = (
                f"ASAR replacement for {relative_path!r} changed size from "
                f"{len(original)} to {len(replacement)}"
            )
            raise AsarIntegrityError(msg)

        new_digest = hashlib.sha256(replacement).hexdigest()
        new_blocks = _block_digests(replacement, block_size)
        if preserve_header_bytes:
            updated_header = _rewrite_header_digests(
                header_bytes,
                old_digests=[digest, *blocks],
                new_digests=[new_digest, *new_blocks],
            )
        else:
            integrity["hash"] = new_digest
            integrity["blocks"] = new_blocks
            updated_header = json.dumps(header, separators=(",", ":")).encode()
        if len(updated_header) != len(header_bytes):
            msg = "ASAR integrity rewrite changed the archive header size"
            raise AsarIntegrityError(msg)

        handle.seek(data_offset + offset)
        handle.write(replacement)
        handle.seek(ASAR_PREFIX_SIZE)
        handle.write(updated_header)

    return hashlib.sha256(updated_header).hexdigest()


def replace_packed_file(
    asar_path: Path,
    relative_path: str,
    transform: Callable[[bytes], bytes],
) -> str:
    """Replace one same-size packed file and canonically refresh its digests."""
    return _replace_packed_file(
        asar_path,
        relative_path,
        transform,
        preserve_header_bytes=False,
    )


def replace_packed_file_preserving_header(
    asar_path: Path,
    relative_path: str,
    transform: Callable[[bytes], bytes],
) -> str:
    """Replace a same-size packed file without reserializing the ASAR header."""
    return _replace_packed_file(
        asar_path,
        relative_path,
        transform,
        preserve_header_bytes=True,
    )


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
