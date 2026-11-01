"""Copy one regular file out of a raw APFS partition image.

Google Drive's DMG (from the 2026-10-15 build on) stores ``GoogleDrive.pkg``
as an ever-cloned inode: its file extents are keyed by the inode's
``private_id`` (29), not its object id (28). 7-Zip rejects the volume as an
"Unsupported feature" and dissect.apfs 1.1's ``INode.open`` looks the extents
up by object id, returning the wrong blocks after the first extent. dissect
still parses the container, object map and directory tree correctly, so this
helper uses it for the path lookup and assembles the data stream itself from
the ``private_id``-keyed extent records, failing closed on any gap, overlap,
short read, compression or encryption.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import BinaryIO

_OBJ_ID_MASK = 0x0FFF_FFFF_FFFF_FFFF
_OBJ_TYPE_SHIFT = 60
_APFS_TYPE_FILE_EXTENT = 8
_J_FILE_EXTENT_LEN_MASK = 0x00FF_FFFF_FFFF_FFFF
_KEY_SIZE = 16
_VALUE_SIZE = 24


class ExtractError(RuntimeError):
    """The APFS file cannot be reproduced exactly."""


@dataclass(frozen=True)
class Extent:
    """One file extent record of an APFS data stream."""

    logical_addr: int
    phys_block_num: int
    length: int


def file_extents(
    records: Iterable[tuple[bytes, bytes]],
    private_id: int,
) -> list[Extent]:
    """Return the ``private_id`` data stream's extents in logical order."""
    extents: list[Extent] = []
    for key, value in records:
        if len(key) < _KEY_SIZE:
            continue
        obj_id_and_type = int.from_bytes(key[:8], "little")
        if obj_id_and_type >> _OBJ_TYPE_SHIFT != _APFS_TYPE_FILE_EXTENT:
            continue
        if obj_id_and_type & _OBJ_ID_MASK != private_id:
            continue
        if len(value) < _VALUE_SIZE:
            msg = f"truncated file extent value for private_id {private_id}"
            raise ExtractError(msg)
        len_and_flags = int.from_bytes(value[:8], "little")
        if len_and_flags & ~_J_FILE_EXTENT_LEN_MASK:
            msg = f"unsupported file extent flags for private_id {private_id}"
            raise ExtractError(msg)
        extents.append(
            Extent(
                logical_addr=int.from_bytes(key[8:16], "little"),
                phys_block_num=int.from_bytes(value[8:16], "little"),
                length=len_and_flags & _J_FILE_EXTENT_LEN_MASK,
            )
        )
    return sorted(extents, key=lambda extent: extent.logical_addr)


def read_stream(
    image: BinaryIO,
    extents: list[Extent],
    *,
    block_size: int,
    size: int,
) -> Iterator[bytes]:
    """Yield exactly ``size`` bytes from contiguous ``extents``."""
    if size == 0:
        if extents:
            msg = "empty file has extents"
            raise ExtractError(msg)
        return
    if any(extent.logical_addr >= size for extent in extents):
        msg = "extent starts beyond end of file"
        raise ExtractError(msg)
    logical = 0
    for extent in extents:
        if extent.logical_addr != logical:
            msg = f"extent gap or overlap at {logical} (next {extent.logical_addr})"
            raise ExtractError(msg)
        if extent.length == 0 or extent.length % block_size:
            msg = f"extent at {logical} is not block aligned: {extent.length}"
            raise ExtractError(msg)
        if extent.phys_block_num == 0:
            msg = f"sparse extent at {logical} is unsupported"
            raise ExtractError(msg)
        wanted = min(extent.length, size - logical)
        image.seek(extent.phys_block_num * block_size)
        chunk = image.read(wanted)
        if len(chunk) != wanted:
            msg = f"short read at logical offset {logical}"
            raise ExtractError(msg)
        yield chunk
        logical += extent.length
        if logical >= size:
            break
    if logical < size:
        msg = f"extents cover {logical} of {size} bytes"
        raise ExtractError(msg)


def extract(image_path: Path, inner_path: str, output: Path) -> None:
    """Copy ``inner_path`` from the sole APFS volume in ``image_path``."""
    from dissect.apfs import APFS  # noqa: PLC0415 # ty: ignore[unresolved-import] -- build-time dependency only

    with image_path.open("rb") as image:
        container = APFS(image)
        volumes = list(container.volumes)
        if len(volumes) != 1:
            msg = f"expected 1 APFS volume, found {len(volumes)}"
            raise ExtractError(msg)
        volume = volumes[0]
        if volume.is_encrypted or volume.is_sealed:
            msg = "encrypted or sealed APFS volumes are unsupported"
            raise ExtractError(msg)
        inode = volume.get(inner_path)
        if not inode.is_file() or inode.is_compressed():
            msg = f"{inner_path} is not an uncompressed regular file"
            raise ExtractError(msg)
        extents = file_extents(volume.cursor().reset().walk(), inode.private_id)
        with output.open("xb") as destination:
            for chunk in read_stream(
                image,
                extents,
                block_size=container.block_size,
                size=inode.size,
            ):
                destination.write(chunk)


def main(argv: list[str] | None = None) -> int:
    """Run ``extract_apfs_file.py IMAGE INNER_PATH OUTPUT``."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:  # noqa: PLR2004 -- fixed CLI arity
        sys.stderr.write("usage: extract_apfs_file.py IMAGE INNER_PATH OUTPUT\n")
        return 2
    extract(Path(args[0]), args[1], Path(args[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
