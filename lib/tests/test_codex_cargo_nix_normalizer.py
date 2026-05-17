"""Tests for the Codex crate2nix Cargo.nix normalizer."""

from __future__ import annotations

from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update.paths import REPO_ROOT

_FIXTURES = REPO_ROOT / "tests" / "nix" / "codex-cargo-nix-normalizer"


def _load_normalizer_module() -> ModuleType:
    module_path = REPO_ROOT / "packages" / "codex" / "normalize_cargo_nix.py"
    return load_module_from_path(module_path, "_codex_normalizer")


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_normalize_adds_root_src_and_rewrites_local_source_paths() -> None:
    """Generated Cargo.nix should gain rootSrc and root-relative sources."""
    module = _load_normalizer_module()

    sample = _fixture("local-source-input.cargo-nix")

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert added_root_src is True
    assert rewrites == 2
    assert_nix_ast_equal(normalized, _fixture("local-source-expected.cargo-nix"))


def test_normalize_rewrites_supported_store_backed_local_crates() -> None:
    """Store-backed local Codex crates should normalize back to ``rootSrc`` paths."""
    module = _load_normalizer_module()

    sample = _fixture("store-backed-local-crates-input.cargo-nix")

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert added_root_src is True
    assert rewrites == 7
    assert_nix_ast_equal(
        normalized,
        _fixture("store-backed-local-crates-expected.cargo-nix"),
    )
