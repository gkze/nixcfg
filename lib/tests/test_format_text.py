"""Tests for the generic text formatter used by `nix fmt`."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from lib.format_text import format_path, main, normalize_text


def test_format_path_normalizes_generic_text_files(tmp_path: Path) -> None:
    """Trim trailing whitespace and normalize EOFs for plain text formats."""
    path = tmp_path / "schema.proto"
    path.write_bytes(b'syntax = "proto3";  \r\n\r\n')

    assert format_path(path) is True
    assert path.read_text(encoding="utf-8") == 'syntax = "proto3";\n'


def test_format_path_preserves_patch_trailing_spaces(tmp_path: Path) -> None:
    """Keep trailing spaces intact inside patch hunks while fixing EOFs."""
    path = tmp_path / "change.patch"
    path.write_bytes(b"+keep  \r\n\r\n")

    assert format_path(path) is True
    assert path.read_bytes() == b"+keep  \n"


def test_normalize_text_handles_empty_and_optional_whitespace_trimming() -> None:
    """The string helper should cover empty input and non-patch whitespace retention."""
    assert normalize_text("\r\n\r\n", trim_trailing_whitespace=True) == ""
    assert normalize_text("keep  \r", trim_trailing_whitespace=False) == "keep  \n"


def test_format_path_returns_false_when_content_is_already_normalized(
    tmp_path: Path,
) -> None:
    """Formatting should report no-op writes for already-normalized files."""
    path = tmp_path / "clean.txt"
    path.write_text("clean\n", encoding="utf-8")

    assert format_path(path) is False


def test_main_uses_sys_argv_when_no_explicit_args(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """CLI entry should default to sys.argv when argv is omitted."""
    path = tmp_path / "notes.txt"
    path.write_text("line  \r\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["format_text.py", str(path)])

    assert main() == 0
    assert path.read_text(encoding="utf-8") == "line\n"


def test_module_main_guard_exits_cleanly(monkeypatch, tmp_path: Path) -> None:
    """Executing the module as a script should hit the ``__main__`` guard."""
    path = tmp_path / "guard.txt"
    path.write_text("guard  \r\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["format_text.py", str(path)])

    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(
            str(Path(main.__code__.co_filename).resolve()), run_name="__main__"
        )

    assert path.read_text(encoding="utf-8") == "guard\n"
