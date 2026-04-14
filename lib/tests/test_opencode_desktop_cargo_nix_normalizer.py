"""Tests for the opencode-desktop crate2nix Cargo.nix normalizer."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.tests._nix_ast import assert_nix_ast_equal


def _load_normalizer_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "opencode-desktop"
        / "normalize_cargo_nix.py"
    )
    return load_module_from_path(module_path, "_opencode_desktop_normalizer")


def test_normalize_rewrites_store_paths_with_src_suffix() -> None:
    """Store paths ending in ``-src`` should still rewrite to ``rootSrc``."""
    module = _load_normalizer_module()

    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = ../../../nix/store/demo-src/packages/desktop/src-tauri;
    };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert added_root_src is True
    assert rewrites == 1
    assert_nix_ast_equal(
        normalized,
        """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
, rootSrc ? ./.
}:
rec {
  foo = {
    src = lib.cleanSourceWith {
      filter = sourceFilter;
      src = "${rootSrc}/packages/desktop/src-tauri";
    };
  };
}
""",
    )


def test_normalize_is_noop_for_checked_in_opencode_desktop_cargo_nix() -> None:
    """The current checked-in Cargo.nix should already be normalized."""
    module = _load_normalizer_module()
    cargo_nix = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "opencode-desktop"
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
