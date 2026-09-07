"""Read arm64 Mach-O load metadata at the native packaging boundary."""

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from macholib import mach_o
from macholib.MachO import MachO
from macholib.ptypes import sizeof

if TYPE_CHECKING:
    from pathlib import Path

_ARM64 = 0x100000C


@dataclass(frozen=True)
class MachOMetadata:
    """Load-command facts shared by executable and runtime-library validation."""

    install_id: str | None
    dependencies: tuple[str, ...]
    rpaths: tuple[str, ...]
    deployment_targets: tuple[tuple[int, int], ...]


def _command_string(offset: int, fixed_size: int, data: bytes) -> str:
    start = offset - fixed_size
    if start < 0 or start >= len(data):
        message = "invalid Mach-O load-command string offset"
        raise ValueError(message)
    end = data.find(b"\0", start)
    if end < 0 or end == start:
        message = "missing or empty Mach-O load-command string"
        raise ValueError(message)
    return data[start:end].decode("utf-8", errors="strict")


def read_macho(path: Path) -> MachOMetadata:
    """Reject malformed or non-arm64 inputs before exposing native load edges."""
    try:
        return _read_macho(path)
    except (OSError, ValueError, struct.error) as error:
        message = f"invalid Mach-O {path}: {error}"
        raise ValueError(message) from error


def _read_macho(path: Path) -> MachOMetadata:
    image = MachO(str(path))
    if len(image.headers) != 1:
        message = "must be arm64-only"
        raise ValueError(message)
    header = image.headers[0].header
    if (
        header.magic != mach_o.MH_MAGIC_64
        or header.cputype != _ARM64
        or header.cpusubtype & 0xFFFFFF != 0
    ):
        message = "must be arm64-only"
        raise ValueError(message)
    install_id = None
    dependencies = []
    rpaths = []
    targets = []
    for command, record, data in image.headers[0].commands:
        if isinstance(record, mach_o.dylib_command):
            value = _command_string(
                int(record.name), sizeof(command) + sizeof(record), data
            )
            if command.cmd == mach_o.LC_ID_DYLIB:
                install_id = value
            else:
                dependencies.append(value)
        elif isinstance(record, mach_o.rpath_command):
            rpaths.append(
                _command_string(
                    int(record.path), sizeof(command) + sizeof(record), data
                )
            )
        elif isinstance(record, mach_o.build_version_command):
            targets.append((int(record.platform), int(record.minos)))
        elif isinstance(record, mach_o.version_min_command):
            platform = 1 if command.cmd == mach_o.LC_VERSION_MIN_MACOSX else 0
            targets.append((platform, int(record.version)))
    return MachOMetadata(install_id, tuple(dependencies), tuple(rpaths), tuple(targets))
