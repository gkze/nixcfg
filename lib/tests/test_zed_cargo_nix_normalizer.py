"""Tests for the Zed crate2nix Cargo.nix normalizer wrapper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from lib.tests._assertions import check


def _load_normalizer_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "zed-editor-nightly"
        / "normalize_cargo_nix.py"
    )
    spec = importlib.util.spec_from_file_location("_zed_normalizer", module_path)
    if spec is None or spec.loader is None:
        msg = f"Could not load normalizer module from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_is_noop_for_checked_in_zed_cargo_nix() -> None:
    """The current checked-in Cargo.nix should already be normalized."""
    module = _load_normalizer_module()
    cargo_nix = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "zed-editor-nightly"
        / "Cargo.nix"
    )

    original = cargo_nix.read_text()
    normalized, rewrites, added_root_src = module.normalize(original)

    check(added_root_src is False)
    check(rewrites == 0)
    check(normalized == original)
