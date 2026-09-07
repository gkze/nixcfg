"""Exercise native metadata decoding with independent Mach-O binary fixtures."""

import struct
from pathlib import Path

import pytest

from lib.macho import read_macho
from lib.tests._macho import build_version, macho, string_command


@pytest.mark.parametrize("kind", [0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023])
def test_reads_dependency_command_variants(tmp_path: Path, kind: int) -> None:
    binary = tmp_path / "library"
    binary.write_bytes(
        macho(
            [
                string_command(0xD, "@rpath/library"),
                string_command(kind, "@rpath/library with spaces.dylib"),
                string_command(0x8000001C, "@loader_path/Frameworks"),
                build_version(),
            ],
            filetype=6,
        )
    )
    result = read_macho(binary)
    assert result.install_id == "@rpath/library"
    assert result.dependencies == ("@rpath/library with spaces.dylib",)
    assert result.rpaths == ("@loader_path/Frameworks",)
    assert result.deployment_targets == ((1, 14 << 16),)


@pytest.mark.parametrize(("kind", "platform"), [(0x24, 1), (0x25, 0)])
def test_reads_old_deployment_commands(
    tmp_path: Path, kind: int, platform: int
) -> None:
    binary = tmp_path / "executable"
    binary.write_bytes(macho([struct.pack("<IIII", kind, 16, 13 << 16, 0)]))
    assert read_macho(binary).deployment_targets == ((platform, 13 << 16),)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not macho",
        struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 2, 2, 0, 0, 0, 0),
        struct.pack("<IIIIIII", 0xFEEDFACE, 0x100000C, 0, 2, 0, 0, 0),
        macho([], cpu=0x1000007),
        struct.pack(">II", 0xCAFEBABE, 0),
        macho([struct.pack("<IIII", 0x8000001C, 16, 0, 0)]),
        macho([struct.pack("<IIII", 0x8000001C, 16, 99, 0)]),
        macho([struct.pack("<III", 0x8000001C, 16, 12) + b"abcd"]),
        macho([struct.pack("<III", 0x8000001C, 16, 12) + b"\0\0\0\0"]),
        macho([struct.pack("<III", 0x8000001C, 16, 12) + b"\xff\0\0\0"]),
        macho([string_command(0xD, "one"), string_command(0xD, "two")]),
        macho([struct.pack("<II", 0x7FFF, 8)]),
        macho([build_version()])[:-1],
    ],
)
def test_rejects_malformed_or_unsupported_binaries(tmp_path: Path, data: bytes) -> None:
    binary = tmp_path / "invalid"
    binary.write_bytes(data)
    with pytest.raises(ValueError, match="invalid Mach-O"):
        read_macho(binary)


def test_missing_binary_has_causal_diagnostic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid Mach-O") as error:
        read_macho(tmp_path / "missing")
    assert isinstance(error.value.__cause__, OSError)


def test_unrelated_commands_do_not_become_dependencies(tmp_path: Path) -> None:
    binary = tmp_path / "executable"
    binary.write_bytes(macho([struct.pack("<II16s", 0x1B, 24, bytes(16))]))
    result = read_macho(binary)
    assert result.install_id is None
    assert result.dependencies == result.rpaths == result.deployment_targets == ()
