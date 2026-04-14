"""Tests for filesystem-based Python module loading helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lib.import_utils import load_module_from_path


def test_load_module_from_path_supports_python_files(tmp_path: Path) -> None:
    """Load a standard ``.py`` module by explicit filesystem path."""
    module_name = "_import_utils_demo"
    module_path = tmp_path / "demo.py"
    module_path.write_text("VALUE = 7\n", encoding="utf-8")

    module = load_module_from_path(module_path, module_name)
    try:
        assert module.VALUE == 7
    finally:
        sys.modules.pop(module_name, None)


def test_load_module_from_path_supports_extensionless_scripts(tmp_path: Path) -> None:
    """Load extensionless repo-style helper scripts through the same helper."""
    module_name = "_import_utils_script"
    script_path = tmp_path / "script"
    script_path.write_text("NAME = 'demo'\n", encoding="utf-8")

    module = load_module_from_path(script_path, module_name)
    try:
        assert module.NAME == "demo"
    finally:
        sys.modules.pop(module_name, None)


def test_load_module_from_path_rolls_back_partial_registration(tmp_path: Path) -> None:
    """Remove failed imports from ``sys.modules`` after execution errors."""
    module_name = "_import_utils_broken"
    broken_path = tmp_path / "broken.py"
    broken_path.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        load_module_from_path(broken_path, module_name)

    assert module_name not in sys.modules
