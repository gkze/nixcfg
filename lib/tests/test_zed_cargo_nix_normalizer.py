"""Tests for the Zed crate2nix Cargo.nix normalizer wrapper."""

from __future__ import annotations

from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_normalizer_module() -> ModuleType:
    module_path = (
        REPO_ROOT / "packages" / "zed-editor-nightly" / "normalize_cargo_nix.py"
    )
    return load_module_from_path(module_path, "_zed_normalizer")


def test_normalize_rewrites_store_paths_and_nixpkgs_config() -> None:
    """Fallback normalization should rewrite store-backed sources and nixpkgs config."""
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
    src = ../../../nix/store/demo-zed-src/crates/zed;
  };
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert added_root_src is True
    assert rewrites == 1
    assert "import nixpkgs { }" in normalized
    assert "rootSrc ? ./." in normalized
    assert 'src = "${rootSrc}/crates/zed";' in normalized
