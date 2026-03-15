"""Tests for the Codex crate2nix Cargo.nix normalizer."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from nix_manipulator import parse

from lib.tests._assertions import check


def _load_normalizer_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "codex"
        / "normalize_cargo_nix.py"
    )
    spec = importlib.util.spec_from_file_location("_codex_normalizer", module_path)
    if spec is None or spec.loader is None:
        msg = f"Could not load normalizer module from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_adds_root_src_and_rewrites_local_source_paths() -> None:
    """Generated Cargo.nix should gain rootSrc and root-relative sources."""
    module = _load_normalizer_module()

    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./cli; };
  bar = { src = ./utils/git; };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    parse(normalized)
    check(added_root_src is True)
    check(rewrites == 2)
    check("rootSrc ? ./." in normalized)
    check('src = "${rootSrc}/cli";' in normalized)
    check('src = "${rootSrc}/utils/git";' in normalized)


def test_normalize_is_noop_for_checked_in_codex_cargo_nix() -> None:
    """The current checked-in Cargo.nix should already be normalized."""
    module = _load_normalizer_module()
    cargo_nix = Path(__file__).resolve().parents[2] / "packages" / "codex" / "Cargo.nix"

    original = cargo_nix.read_text()
    normalized, rewrites, added_root_src = module.normalize(original)

    check(added_root_src is False)
    check(rewrites == 0)
    check(normalized == original)
