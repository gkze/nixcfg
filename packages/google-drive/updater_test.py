"""Tests for the Google Drive package build helpers."""

import io
from types import ModuleType

import pytest

from lib.tests._updater_helpers import load_repo_module
from lib.update.paths import REPO_ROOT

_BLOCK = 16
_FILE_EXTENT = 8


def _module() -> ModuleType:
    return load_repo_module(
        "packages/google-drive/extract_apfs_file.py",
        "google_drive_extract_apfs_file_test",
    )


def _extent_record(
    oid: int,
    logical: int,
    block: int,
    length: int,
    *,
    record_type: int = _FILE_EXTENT,
) -> tuple[bytes, bytes]:
    key = ((record_type << 60) | oid).to_bytes(8, "little") + logical.to_bytes(
        8, "little"
    )
    value = (
        length.to_bytes(8, "little")
        + block.to_bytes(8, "little")
        + (0).to_bytes(8, "little")
    )
    return key, value


def _image() -> io.BytesIO:
    return io.BytesIO(b"".join(bytes([index]) * _BLOCK for index in range(8)))


def test_extents_are_read_by_private_id_not_inode_oid() -> None:
    """2026-10-15 GoogleDrive.pkg: oid 28, data stream keyed by private_id 29."""
    module = _module()
    records = [
        _extent_record(28, 0, 1, _BLOCK),
        _extent_record(29, _BLOCK, 5, _BLOCK),
        _extent_record(29, 0, 3, _BLOCK),
        _extent_record(29, 0, 7, _BLOCK, record_type=3),
    ]

    extents = module.file_extents(records, 29)
    data = b"".join(
        module.read_stream(_image(), extents, block_size=_BLOCK, size=_BLOCK + 4)
    )

    assert [extent.logical_addr for extent in extents] == [0, _BLOCK]
    assert data == bytes([3]) * _BLOCK + bytes([5]) * 4


@pytest.mark.parametrize(
    ("records", "size", "message"),
    [
        ([_extent_record(29, 0, 1, _BLOCK)], 2 * _BLOCK, "cover 16 of 32"),
        (
            [_extent_record(29, 0, 1, _BLOCK), _extent_record(29, 2 * _BLOCK, 2, 16)],
            3 * _BLOCK,
            "gap or overlap",
        ),
        ([_extent_record(29, 0, 0, _BLOCK)], _BLOCK, "sparse"),
        ([_extent_record(29, 0, 1, _BLOCK - 1)], 4, "not block aligned"),
        (
            [_extent_record(29, 0, 1, _BLOCK), _extent_record(29, _BLOCK, 2, 16)],
            _BLOCK,
            "beyond end of file",
        ),
        ([_extent_record(29, 0, 100, _BLOCK)], _BLOCK, "short read"),
        ([_extent_record(28, 0, 1, _BLOCK)], _BLOCK, "cover 0 of 16"),
    ],
)
def test_incomplete_or_ambiguous_streams_fail_closed(
    records: list[tuple[bytes, bytes]],
    size: int,
    message: str,
) -> None:
    """Never emit a partial or reordered installer package."""
    module = _module()
    extents = module.file_extents(records, 29)

    with pytest.raises(module.ExtractError, match=message):
        b"".join(module.read_stream(_image(), extents, block_size=_BLOCK, size=size))


def test_flagged_extents_are_rejected() -> None:
    """Extent flag bits are not understood and must not be ignored."""
    module = _module()
    key, value = _extent_record(29, 0, 1, _BLOCK)
    flagged = ((1 << 56) | _BLOCK).to_bytes(8, "little") + value[8:]

    with pytest.raises(module.ExtractError, match="unsupported file extent flags"):
        module.file_extents([(key, flagged)], 29)


def test_package_extracts_the_installer_with_the_repo_apfs_helper() -> None:
    """The DMG's installer must come from the private_id-aware extractor."""
    default_nix = (REPO_ROOT / "packages/google-drive/default.nix").read_text(
        encoding="utf-8"
    )

    assert "ps.dissect-apfs" in default_nix
    assert '7zz x -tdmg -y "$src" -o"$unpack_dir/dmg" \'*.apfs\'' in default_nix
    assert "${./extract_apfs_file.py}" in default_nix
    assert '/GoogleDrive.pkg "$unpack_dir/GoogleDrive.pkg"' in default_nix
    assert "Install Google Drive/GoogleDrive.pkg" not in default_nix
