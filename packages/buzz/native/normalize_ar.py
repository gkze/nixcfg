"""Normalize metadata in a Darwin archive without changing member payloads."""

import os
import sys
import tempfile
from pathlib import Path

_MAGIC = b"!<arch>\n"
_HEADER_SIZE = 60
_HEADER_TRAILER = b"`\n"
_EXPECTED_ARGUMENT_COUNT = 2
_MISSING_MAGIC = "missing archive magic"
_EMPTY_ARCHIVE = "archive has no members"
_TRUNCATED_HEADER = "truncated archive header"
_INVALID_TRAILER = "invalid archive header trailer"
_INVALID_SIZE = "invalid archive member size"
_TRUNCATED_PAYLOAD = "truncated archive member payload"
_TRUNCATED_PADDING = "truncated archive member padding"
_INVALID_PADDING = "invalid archive member padding"
_USAGE = "usage: normalize_ar.py ARCHIVE"


def _zero_field(width: int) -> bytes:
    return b"0".ljust(width, b" ")


def normalize_archive_bytes(data: bytes) -> bytes:
    """Return *data* with every archive member's time and owners zeroed."""
    if not data.startswith(_MAGIC):
        raise ValueError(_MISSING_MAGIC)

    normalized = bytearray(data)
    offset = len(_MAGIC)
    member_count = 0
    while offset < len(data):
        header_end = offset + _HEADER_SIZE
        if header_end > len(data):
            raise ValueError(_TRUNCATED_HEADER)
        if data[offset + 58 : header_end] != _HEADER_TRAILER:
            raise ValueError(_INVALID_TRAILER)

        raw_size = data[offset + 48 : offset + 58]
        try:
            size_text = raw_size.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ValueError(_INVALID_SIZE) from error
        if not size_text.isdecimal():
            raise ValueError(_INVALID_SIZE)
        size = int(size_text)
        payload_end = header_end + size
        if payload_end > len(data):
            raise ValueError(_TRUNCATED_PAYLOAD)

        next_offset = payload_end
        if size & 1:
            if next_offset >= len(data):
                raise ValueError(_TRUNCATED_PADDING)
            if data[next_offset : next_offset + 1] != b"\n":
                raise ValueError(_INVALID_PADDING)
            next_offset += 1

        normalized[offset + 16 : offset + 28] = _zero_field(12)
        normalized[offset + 28 : offset + 34] = _zero_field(6)
        normalized[offset + 34 : offset + 40] = _zero_field(6)
        member_count += 1
        offset = next_offset

    if member_count == 0:
        raise ValueError(_EMPTY_ARCHIVE)
    return bytes(normalized)


def normalize_archive(path: Path) -> None:
    """Atomically replace *path* with its normalized, read-only archive bytes."""
    normalized = normalize_archive_bytes(path.read_bytes())
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(normalized)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o444)
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    """Normalize the one archive supplied by the build."""
    if len(sys.argv) != _EXPECTED_ARGUMENT_COUNT:
        raise SystemExit(_USAGE)
    normalize_archive(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
