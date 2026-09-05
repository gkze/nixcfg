"""Tests for Electron ASAR header integrity helpers."""

import hashlib
import json
import plistlib
import runpy
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lib import asar_integrity


def _write_asar(path: Path, header: bytes) -> None:
    prefix = bytearray(16)
    struct.pack_into("<I", prefix, 12, len(header))
    path.write_bytes(bytes(prefix) + header + b"payload")


def _write_raw_asar(
    path: Path,
    header_value: object,
    payload: bytes,
    *,
    compact: bool = True,
    archive_header_size: int | None = None,
) -> None:
    separators = (",", ":") if compact else None
    header = json.dumps(header_value, separators=separators).encode()
    resolved_archive_size = (
        8 + len(header) if archive_header_size is None else archive_header_size
    )
    prefix = struct.pack(
        "<IIII",
        4,
        resolved_archive_size,
        4 + len(header),
        len(header),
    )
    path.write_bytes(prefix + header + payload)


def _write_packed_asar(
    path: Path,
    relative_path: str,
    payload: bytes,
    *,
    block_size: int = 4,
) -> dict[str, Any]:
    blocks = [
        hashlib.sha256(payload[offset : offset + block_size]).hexdigest()
        for offset in range(0, len(payload), block_size)
    ]
    entry: dict[str, object] = {
        "size": len(payload),
        "offset": "0",
        "integrity": {
            "algorithm": "SHA256",
            "hash": hashlib.sha256(payload).hexdigest(),
            "blockSize": block_size,
            "blocks": blocks,
        },
    }
    files: dict[str, object] = {}
    current = files
    components = relative_path.split("/")
    for component in components[:-1]:
        child: dict[str, object] = {"files": {}}
        current[component] = child
        current = cast("dict[str, object]", child["files"])
    current[components[-1]] = entry
    header = {"files": files}
    _write_raw_asar(path, header, payload)
    return header


def _test_entry(header: dict[str, Any], relative_path: str) -> dict[str, Any]:
    node = header
    for component in relative_path.split("/"):
        files = cast("dict[str, Any]", node["files"])
        node = cast("dict[str, Any]", files[component])
    return node


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


def test_replace_packed_file_preserves_size_and_refreshes_integrity(
    tmp_path: Path,
) -> None:
    """A policy patch should remain loadable under every ASAR integrity layer."""
    asar_path = tmp_path / "app.asar"
    original = b"before mutable updater after"
    _write_packed_asar(asar_path, "dist/main.js", original)
    original_size = asar_path.stat().st_size

    digest = asar_integrity.replace_packed_file(
        asar_path,
        "dist/main.js",
        lambda payload: payload.replace(b"mutable", b"managed"),
    )

    assert asar_path.stat().st_size == original_size
    assert digest == asar_integrity.asar_header_hash(asar_path)
    assert (
        asar_integrity.read_packed_file(asar_path, "dist/main.js")
        == b"before managed updater after"
    )


def test_packed_file_paths_lists_only_archive_payloads(tmp_path: Path) -> None:
    """Discovery should recurse through directories and omit links or unpacked files."""
    asar_path = tmp_path / "app.asar"
    payload = b"packed payload"
    header = _write_packed_asar(asar_path, "dist/assets/index.js", payload)
    files = cast("dict[str, object]", header["files"])
    files["native.node"] = {"unpacked": True}
    files["renderer-link.js"] = {"link": "dist/assets/index.js"}
    _write_raw_asar(asar_path, header, payload)

    assert asar_integrity.packed_file_paths(asar_path) == ("dist/assets/index.js",)


@pytest.mark.parametrize(
    ("header", "message"),
    [
        ({"files": []}, "malformed file inventory"),
        ({"files": {"index.js": 1}}, "is not an object"),
    ],
)
def test_packed_file_paths_rejects_malformed_inventories(
    tmp_path: Path,
    header: object,
    message: str,
) -> None:
    """Malformed ASAR trees must not produce a partial candidate inventory."""
    asar_path = tmp_path / "app.asar"
    _write_raw_asar(asar_path, header, b"")

    with pytest.raises(asar_integrity.AsarIntegrityError, match=message):
        asar_integrity.packed_file_paths(asar_path)


def test_replace_packed_file_preserving_header_keeps_vendor_serialization(
    tmp_path: Path,
) -> None:
    """Digest updates may not reserialize a vendor ASAR header."""
    asar_path = tmp_path / "app.asar"
    original = b"before mutable updater after"
    header = _write_packed_asar(asar_path, "dist/main.js", original)
    _write_raw_asar(asar_path, header, original, compact=False)
    original_header = asar_integrity.read_asar_header(asar_path)
    original_size = asar_path.stat().st_size

    digest = asar_integrity.replace_packed_file_preserving_header(
        asar_path,
        "dist/main.js",
        lambda payload: payload.replace(b"mutable", b"managed"),
    )

    updated_header = asar_integrity.read_asar_header(asar_path)
    assert asar_path.stat().st_size == original_size
    assert len(updated_header) == len(original_header)
    assert b'"files": {' in updated_header
    assert digest == hashlib.sha256(updated_header).hexdigest()
    assert (
        asar_integrity.read_packed_file(asar_path, "dist/main.js")
        == b"before managed updater after"
    )


def test_replace_packed_file_preserving_header_rejects_ambiguous_digests(
    tmp_path: Path,
) -> None:
    """One old digest cannot represent two different replacement blocks."""
    asar_path = tmp_path / "app.asar"
    _write_packed_asar(asar_path, "main.js", b"aaaaaaaa")
    original = asar_path.read_bytes()

    with pytest.raises(
        asar_integrity.AsarIntegrityError,
        match="reused one old integrity digest",
    ):
        asar_integrity.replace_packed_file_preserving_header(
            asar_path,
            "main.js",
            lambda _payload: b"aaaabbbb",
        )

    assert asar_path.read_bytes() == original


def test_preserving_header_rejects_digest_count_mismatch_without_mutation(
    tmp_path: Path,
) -> None:
    """A digest repeated outside integrity metadata must abort before mutation."""
    asar_path = tmp_path / "app.asar"
    original_payload = b"before mutable updater after"
    header = _write_packed_asar(asar_path, "dist/main.js", original_payload)
    entry = _test_entry(header, "dist/main.js")
    integrity = cast("dict[str, Any]", entry["integrity"])
    header["unrelatedDigest"] = integrity["hash"]
    _write_raw_asar(asar_path, header, original_payload)
    original_archive = asar_path.read_bytes()

    with pytest.raises(
        asar_integrity.AsarIntegrityError,
        match=r"expected 1 ASAR integrity entries .* found 2",
    ):
        asar_integrity.replace_packed_file_preserving_header(
            asar_path,
            "dist/main.js",
            lambda payload: payload.replace(b"mutable", b"managed"),
        )

    assert asar_path.read_bytes() == original_archive


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (b"short", "too short"),
        (struct.pack("<IIII", 4, 108, 104, 100) + b"{}", "header is truncated"),
        (struct.pack("<IIII", 4, 10, 6, 2) + b"xx", "not valid JSON"),
        (struct.pack("<IIII", 4, 10, 6, 2) + b"[]", "not an object"),
    ],
)
def test_read_packed_file_rejects_malformed_layouts(
    tmp_path: Path,
    archive: bytes,
    message: str,
) -> None:
    """Packed-file access must fail before trusting malformed ASAR layouts."""
    asar_path = tmp_path / "app.asar"
    asar_path.write_bytes(archive)

    with pytest.raises(asar_integrity.AsarIntegrityError, match=message):
        asar_integrity.read_packed_file(asar_path, "main.js")


def test_read_packed_file_rejects_overlapping_data(tmp_path: Path) -> None:
    """A forged data offset must not permit reads from inside the header."""
    asar_path = tmp_path / "app.asar"
    _write_raw_asar(
        asar_path,
        {"files": {}},
        b"",
        archive_header_size=0,
    )

    with pytest.raises(asar_integrity.AsarIntegrityError, match="overlaps"):
        asar_integrity.read_packed_file(asar_path, "main.js")


def test_read_packed_file_rejects_missing_and_unpacked_entries(
    tmp_path: Path,
) -> None:
    """The mutation helper owns packed payloads only."""
    asar_path = tmp_path / "app.asar"
    _write_raw_asar(asar_path, {"files": {}}, b"")
    with pytest.raises(asar_integrity.AsarIntegrityError, match="missing packed"):
        asar_integrity.read_packed_file(asar_path, "main.js")

    header = _write_packed_asar(asar_path, "main.js", b"payload")
    _test_entry(header, "main.js")["unpacked"] = True
    _write_raw_asar(asar_path, header, b"payload")
    with pytest.raises(asar_integrity.AsarIntegrityError, match="outside"):
        asar_integrity.read_packed_file(asar_path, "main.js")


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"size": "bad"}, "invalid size or offset"),
        ({"size": -1}, "invalid size or offset"),
        ({"offset": -1}, "invalid size or offset"),
        ({"integrity": None}, "lacks SHA256"),
        ({"integrity": {"algorithm": "SHA1"}}, "lacks SHA256"),
        (
            {"integrity": {"algorithm": "SHA256", "hash": 1}},
            "malformed integrity",
        ),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": "digest",
                    "blockSize": "4",
                }
            },
            "malformed integrity",
        ),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": "digest",
                    "blockSize": 0,
                }
            },
            "malformed integrity",
        ),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": "digest",
                    "blockSize": 4,
                    "blocks": "bad",
                }
            },
            "malformed integrity",
        ),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": "digest",
                    "blockSize": 4,
                    "blocks": [1],
                }
            },
            "malformed integrity",
        ),
    ],
)
def test_read_packed_file_rejects_invalid_entry_metadata(
    tmp_path: Path,
    updates: dict[str, object],
    message: str,
) -> None:
    """Every offset and digest component is required before payload access."""
    asar_path = tmp_path / "app.asar"
    header = _write_packed_asar(asar_path, "main.js", b"payload")
    _test_entry(header, "main.js").update(updates)
    _write_raw_asar(asar_path, header, b"payload")

    with pytest.raises(asar_integrity.AsarIntegrityError, match=message):
        asar_integrity.read_packed_file(asar_path, "main.js")


@pytest.mark.parametrize(
    ("updates", "payload", "message"),
    [
        ({"size": 8}, b"payload", "is truncated"),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": "0" * 64,
                    "blockSize": 4,
                    "blocks": [],
                }
            },
            b"payload",
            "integrity hash",
        ),
        (
            {
                "integrity": {
                    "algorithm": "SHA256",
                    "hash": hashlib.sha256(b"payload").hexdigest(),
                    "blockSize": 4,
                    "blocks": ["0" * 64, "0" * 64],
                }
            },
            b"payload",
            "block hashes",
        ),
    ],
)
def test_read_packed_file_rejects_payload_integrity_mismatches(
    tmp_path: Path,
    updates: dict[str, object],
    payload: bytes,
    message: str,
) -> None:
    """Corrupt packed payloads must never reach a policy transform."""
    asar_path = tmp_path / "app.asar"
    header = _write_packed_asar(asar_path, "main.js", payload)
    _test_entry(header, "main.js").update(updates)
    _write_raw_asar(asar_path, header, payload)

    with pytest.raises(asar_integrity.AsarIntegrityError, match=message):
        asar_integrity.read_packed_file(asar_path, "main.js")


@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (lambda _payload: "not bytes", "must return bytes"),
        (lambda payload: payload + b"x", "changed size"),
    ],
)
def test_replace_packed_file_rejects_invalid_transform_results(
    tmp_path: Path,
    transform: object,
    message: str,
) -> None:
    """A failed transform contract must leave the archive byte-for-byte intact."""
    asar_path = tmp_path / "app.asar"
    _write_packed_asar(asar_path, "main.js", b"payload")
    original = asar_path.read_bytes()

    with pytest.raises(asar_integrity.AsarIntegrityError, match=message):
        asar_integrity.replace_packed_file(
            asar_path,
            "main.js",
            cast("Any", transform),
        )

    assert asar_path.read_bytes() == original


def test_replace_packed_file_rejects_noncanonical_header_growth(
    tmp_path: Path,
) -> None:
    """Re-encoding must never move packed payload offsets implicitly."""
    asar_path = tmp_path / "app.asar"
    header = _write_packed_asar(asar_path, "main.js", b"payload")
    _write_raw_asar(asar_path, header, b"payload", compact=False)
    original = asar_path.read_bytes()

    with pytest.raises(asar_integrity.AsarIntegrityError, match="header size"):
        asar_integrity.replace_packed_file(
            asar_path,
            "main.js",
            lambda payload: payload,
        )

    assert asar_path.read_bytes() == original


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


def test_plist_shape_errors_are_targeted(tmp_path: Path) -> None:
    """Malformed plist integrity entries should fail before launch time."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    _write_asar(asar_path, b'{"files":{}}')

    with plist_path.open("wb") as handle:
        plistlib.dump(["not", "a", "dict"], handle)
    with pytest.raises(asar_integrity.AsarIntegrityError, match="plist dictionary"):
        asar_integrity.write_info_plist_hash(plist_path, asar_path)

    with plist_path.open("wb") as handle:
        plistlib.dump({"ElectronAsarIntegrity": "bad"}, handle)
    with pytest.raises(
        asar_integrity.AsarIntegrityError,
        match="Expected ElectronAsarIntegrity dictionary",
    ):
        asar_integrity.write_info_plist_hash(plist_path, asar_path)
    with pytest.raises(asar_integrity.AsarIntegrityError, match="Missing"):
        asar_integrity.check_info_plist_hash(plist_path, asar_path)

    with plist_path.open("wb") as handle:
        plistlib.dump(
            {"ElectronAsarIntegrity": {"Resources/app.asar": "bad"}},
            handle,
        )
    with pytest.raises(asar_integrity.AsarIntegrityError, match="must be a dictionary"):
        asar_integrity.check_info_plist_hash(plist_path, asar_path)

    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "ElectronAsarIntegrity": {
                    "Resources/app.asar": {
                        "algorithm": "SHA256",
                        "hash": 1,
                    }
                }
            },
            handle,
        )
    with pytest.raises(asar_integrity.AsarIntegrityError, match="non-string hash"):
        asar_integrity.check_info_plist_hash(plist_path, asar_path)


def test_main_sets_checks_and_reports_errors(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The CLI should expose both integrity operations and user-facing errors."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    _write_asar(asar_path, b'{"files":{}}')
    with plist_path.open("wb") as handle:
        plistlib.dump({}, handle)

    assert (
        asar_integrity.main([
            "set-info-plist-hash",
            str(plist_path),
            str(asar_path),
            "--key",
            "Custom/app.asar",
        ])
        == 0
    )
    assert "updated" in capsys.readouterr().out

    assert (
        asar_integrity.main([
            "check-info-plist-hash",
            str(plist_path),
            str(asar_path),
            "--key",
            "Custom/app.asar",
        ])
        == 0
    )
    assert "verified" in capsys.readouterr().out

    assert (
        asar_integrity.main([
            "check-info-plist-hash",
            str(plist_path),
            str(asar_path),
        ])
        == 1
    )
    assert "Missing ElectronAsarIntegrity" in capsys.readouterr().err


def test_main_reports_unknown_commands(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive command dispatch should reject unexpected parser results."""
    monkeypatch.setattr(
        asar_integrity,
        "_parse_args",
        lambda _argv: SimpleNamespace(command="unknown"),
    )

    assert asar_integrity.main([]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_module_entrypoint_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as a script should propagate ``main``'s status."""
    plist_path = tmp_path / "Info.plist"
    asar_path = tmp_path / "app.asar"
    _write_asar(asar_path, b'{"files":{}}')
    with plist_path.open("wb") as handle:
        plistlib.dump({}, handle)
    monkeypatch.setattr(
        "sys.argv",
        ["asar_integrity.py", "set-info-plist-hash", str(plist_path), str(asar_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path("lib/asar_integrity.py", run_name="__main__")

    assert exc_info.value.code == 0
