"""Tests for the shared crate2nix Cargo.nix normalizer helpers."""

from __future__ import annotations

import re

from lib.cargo_nix_normalizer import _normalize_with_fallback, normalize
from lib.tests._nix_ast import assert_nix_ast_equal


def test_normalize_rewrites_local_workspace_paths() -> None:
    """AST-driven rewrites should convert local paths to rootSrc-relative strings."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./crates/foo; };
  bar = { src = ./vendor/v8; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("crates", "vendor"),
    )

    assert added_root_src is True
    assert rewrites == 2
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
  foo = { src = "${rootSrc}/crates/foo"; };
  bar = { src = "${rootSrc}/vendor/v8"; };
}
""",
    )


def test_normalize_rewrites_exact_local_workspace_roots() -> None:
    """AST-driven rewrites should handle exact ``./dir`` source bindings too."""
    sample = """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { config = {}; }
, crateConfig
  ? if builtins.pathExists ./crate-config.nix
    then pkgs.callPackage ./crate-config.nix {}
    else {}
}:
rec {
  foo = { src = ./cli; };
}
"""

    normalized, rewrites, added_root_src = normalize(
        sample,
        local_path_prefixes=("cli",),
    )

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
  foo = { src = "${rootSrc}/cli"; };
}
""",
    )


def test_normalize_fallback_rewrites_store_paths() -> None:
    """Fallback regex rewrites should handle crate2nix store-path source bindings."""
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
      src = ../../nix/store/abc-source/crates/foo;
    };
  };
}
"""

    pattern = re.compile(
        r"(?P<needle>(?:\.\./)+nix/store/[^/]+-source/(?P<suffix>[^;]+))"
    )

    normalized, rewrites, added_root_src = _normalize_with_fallback(
        sample,
        local_path_prefixes=("crates",),
        fallback_patterns=(pattern,),
        rewrite_nixpkgs_config=True,
    )

    assert added_root_src is True
    assert rewrites == 1
    assert_nix_ast_equal(
        normalized,
        """{ nixpkgs ? <nixpkgs>
, pkgs ? import nixpkgs { }
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
      src = "${rootSrc}/crates/foo";
    };
  };
}
""",
    )
