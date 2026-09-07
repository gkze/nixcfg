"""Small real Mach-O fixtures, encoded independently of macholib's reader."""

import struct
from collections.abc import Sequence

ARM64 = 0x100000C


def string_command(kind: int, value: str) -> bytes:
    """Encode a dylib or rpath load command with an aligned C string."""
    fixed = 12 if kind == 0x8000001C else 24
    data = value.encode() + b"\0"
    size = (fixed + len(data) + 7) & ~7
    return (
        struct.pack("<III", kind, size, fixed)
        + bytes(fixed - 12)
        + data
        + bytes(size - fixed - len(data))
    )


def build_version(version: int = 14 << 16, platform: int = 1) -> bytes:
    return struct.pack("<IIIIII", 0x32, 24, platform, version, 0, 0)


def macho(commands: Sequence[bytes], *, cpu: int = ARM64, filetype: int = 2) -> bytes:
    return struct.pack(
        "<IIIIIIII",
        0xFEEDFACF,
        cpu,
        0,
        filetype,
        len(commands),
        sum(map(len, commands)),
        0,
        0,
    ) + b"".join(commands)
