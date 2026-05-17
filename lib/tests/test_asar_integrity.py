"""Tests for Electron ASAR header integrity helpers."""

from __future__ import annotations

import hashlib
import plistlib
import struct
from pathlib import Path

import pytest

from lib import asar_integrity


def _write_asar(path: Path, header: bytes) -> None:
    prefix = bytearray(16)
    struct.pack_into("<I", prefix, 12, len(header))
    path.write_bytes(bytes(prefix) + header + b"payload")


def _read_plist(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    assert isinstance(payload, dict)
    return payload


def test_write_info_plist_hash_uses_asar_header_bytes(tmp_path: Path) -> None:
    """Electron validates the ASAR header hash, not the whole archive hash."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    header = b'{"files":{"index.js":{"size":0,"offset":"0"}}}'
    _write_asar(asar_path, header)
    with plist_path.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.example.App"}, handle)

    digest = asar_integrity.write_info_plist_hash(plist_path, asar_path)

    assert digest == hashlib.sha256(header).hexdigest()
    assert digest != hashlib.sha256(asar_path.read_bytes()).hexdigest()
    assert _read_plist(plist_path)["ElectronAsarIntegrity"] == {
        "Resources/app.asar": {
            "algorithm": "SHA256",
            "hash": digest,
        }
    }
    assert asar_integrity.check_info_plist_hash(plist_path, asar_path) == digest


def test_check_info_plist_hash_rejects_mismatches(tmp_path: Path) -> None:
    """Launch-breaking mismatches should fail with a targeted error."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    _write_asar(asar_path, b'{"files":{}}')
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "ElectronAsarIntegrity": {
                    "Resources/app.asar": {
                        "algorithm": "SHA256",
                        "hash": "bad",
                    }
                }
            },
            handle,
        )

    with pytest.raises(
        asar_integrity.AsarIntegrityError,
        match="ASAR ElectronAsarIntegrity mismatch",
    ):
        asar_integrity.check_info_plist_hash(plist_path, asar_path)


def test_check_info_plist_hash_rejects_non_sha256_algorithm(tmp_path: Path) -> None:
    """Electron ASAR integrity entries should keep the SHA256 contract explicit."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    header = b'{"files":{}}'
    _write_asar(asar_path, header)
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "ElectronAsarIntegrity": {
                    "Resources/app.asar": {
                        "algorithm": "SHA1",
                        "hash": hashlib.sha256(header).hexdigest(),
                    }
                }
            },
            handle,
        )

    with pytest.raises(
        asar_integrity.AsarIntegrityError,
        match="must use SHA256",
    ):
        asar_integrity.check_info_plist_hash(plist_path, asar_path)


def test_read_asar_header_rejects_truncated_archives(tmp_path: Path) -> None:
    """Malformed ASAR files should produce explicit user-facing errors."""
    short_asar = tmp_path / "short.asar"
    short_asar.write_bytes(b"short")

    with pytest.raises(asar_integrity.AsarIntegrityError, match="too short"):
        asar_integrity.read_asar_header(short_asar)

    truncated_asar = tmp_path / "truncated.asar"
    prefix = bytearray(16)
    struct.pack_into("<I", prefix, 12, 100)
    truncated_asar.write_bytes(bytes(prefix) + b"{}")

    with pytest.raises(asar_integrity.AsarIntegrityError, match="header is truncated"):
        asar_integrity.read_asar_header(truncated_asar)
