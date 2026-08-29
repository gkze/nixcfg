"""Tests for Buzz's deterministic Darwin archive normalizer."""

import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "packages/buzz/native/normalize_ar.py"
)
_MAGIC = b"!<arch>\n"


def _load_script() -> dict[str, Any]:
    return runpy.run_path(str(_SCRIPT_PATH))


def _field(value: bytes, width: int) -> bytes:
    assert len(value) <= width
    return value.ljust(width, b" ")


def _member(name: bytes, payload: bytes) -> bytes:
    member_payload = name + payload
    header = b"".join((
        _field(f"#1/{len(name)}".encode(), 16),
        _field(b"123456", 12),
        _field(b"351", 6),
        _field(b"350", 6),
        _field(b"100644", 8),
        _field(str(len(member_payload)).encode(), 10),
        b"`\n",
    ))
    assert len(header) == 60
    padding = b"\n" if len(member_payload) & 1 else b""
    return header + member_payload + padding


def _archive() -> bytes:
    return (
        _MAGIC
        + _member(b"__.SYMDEF SORTED\0\0\0\0", b"x")
        + _member(
            b"prelinked_objects.o\0",
            b"payload",
        )
    )


def _normalizer(namespace: dict[str, Any]) -> Callable[[bytes], bytes]:
    return cast("Callable[[bytes], bytes]", namespace["normalize_archive_bytes"])


def test_normalize_archive_bytes_changes_only_time_and_owners() -> None:
    """Normalize every member while preserving names, modes, sizes, and payloads."""
    source = _archive()
    normalized = _normalizer(_load_script())(source)
    expected = bytearray(source)
    offset = len(_MAGIC)
    while offset < len(expected):
        size = int(expected[offset + 48 : offset + 58].decode().strip())
        expected[offset + 16 : offset + 28] = b"0".ljust(12, b" ")
        expected[offset + 28 : offset + 34] = b"0".ljust(6, b" ")
        expected[offset + 34 : offset + 40] = b"0".ljust(6, b" ")
        offset += 60 + size + (size & 1)

    assert normalized == bytes(expected)


def test_normalize_archive_replaces_atomically_and_sets_read_only_mode(
    tmp_path: Path,
) -> None:
    """The filesystem entry receives normalized bytes and deterministic mode."""
    namespace = _load_script()
    normalize_archive = cast(
        "Callable[[Path], None]",
        namespace["normalize_archive"],
    )
    archive = tmp_path / "libonnxruntime.a"
    archive.write_bytes(_archive())

    normalize_archive(archive)

    assert archive.read_bytes() == _normalizer(namespace)(_archive())
    assert archive.stat().st_mode & 0o777 == 0o444
    assert list(tmp_path.iterdir()) == [archive]


def test_normalize_archive_cli_and_main_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose the normalizer as a strict one-argument build helper."""
    namespace = _load_script()
    main = cast("Callable[[], int]", namespace["main"])
    archive = tmp_path / "libonnxruntime.a"
    archive.write_bytes(_archive())
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT_PATH), str(archive)])
    assert main() == 0

    monkeypatch.setattr(sys, "argv", [str(_SCRIPT_PATH)])
    with pytest.raises(SystemExit, match="usage: normalize_ar.py ARCHIVE"):
        main()

    monkeypatch.setattr(sys, "argv", [str(_SCRIPT_PATH), str(archive)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(_SCRIPT_PATH), run_name="__main__")
    assert exc.value.code == 0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not-an-archive", "missing archive magic"),
        (_MAGIC, "archive has no members"),
        (_MAGIC + b"short", "truncated archive header"),
        (
            _MAGIC + _member(b"x", b"payload")[:-1],
            "truncated archive member payload",
        ),
        (
            _MAGIC + _member(b"x", b"")[:-1],
            "truncated archive member padding",
        ),
    ],
)
def test_normalize_archive_bytes_rejects_truncated_inputs(
    data: bytes,
    message: str,
) -> None:
    """Reject malformed archives instead of partially normalizing them."""
    with pytest.raises(ValueError, match=message):
        _normalizer(_load_script())(data)


def test_normalize_archive_bytes_rejects_header_and_padding_corruption() -> None:
    """Reject malformed trailers, sizes, and odd-member padding."""
    normalize = _normalizer(_load_script())
    valid = bytearray(_MAGIC + _member(b"x", b""))

    bad_trailer = valid.copy()
    bad_trailer[len(_MAGIC) + 58 : len(_MAGIC) + 60] = b"xx"
    with pytest.raises(ValueError, match="invalid archive header trailer"):
        normalize(bytes(bad_trailer))

    bad_size = valid.copy()
    bad_size[len(_MAGIC) + 48 : len(_MAGIC) + 58] = b"bad       "
    with pytest.raises(ValueError, match="invalid archive member size"):
        normalize(bytes(bad_size))

    non_ascii_size = valid.copy()
    non_ascii_size[len(_MAGIC) + 48 : len(_MAGIC) + 58] = b"\xff         "
    with pytest.raises(ValueError, match="invalid archive member size"):
        normalize(bytes(non_ascii_size))

    bad_padding = valid.copy()
    bad_padding[-1:] = b"x"
    with pytest.raises(ValueError, match="invalid archive member padding"):
        normalize(bytes(bad_padding))


def test_normalize_archive_bytes_accepts_even_member_size() -> None:
    """Do not consume a padding byte after an even-sized archive member."""
    source = _MAGIC + _member(b"xy", b"")
    normalized = _normalizer(_load_script())(source)

    assert len(normalized) == len(source)
    assert normalized.endswith(b"xy")


def test_normalize_archive_removes_temporary_file_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the original archive intact if durable temporary writing fails."""
    namespace = _load_script()
    normalize_archive = cast(
        "Callable[[Path], None]",
        namespace["normalize_archive"],
    )
    archive = tmp_path / "libonnxruntime.a"
    original = _archive()
    archive.write_bytes(original)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(namespace["os"], "fsync", fail_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        normalize_archive(archive)

    assert archive.read_bytes() == original
    assert list(tmp_path.iterdir()) == [archive]
