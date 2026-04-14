"""Tests for the Zed crate2nix Cargo.nix normalizer wrapper."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path


def _load_normalizer_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "zed-editor-nightly"
        / "normalize_cargo_nix.py"
    )
    return load_module_from_path(module_path, "_zed_normalizer")


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

    assert added_root_src is False
    assert rewrites == 0

    _normalized_again, rewrites_again, added_root_src_again = module.normalize(
        normalized
    )

    assert added_root_src_again is False
    assert rewrites_again == 0
