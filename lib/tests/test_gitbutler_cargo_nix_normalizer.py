"""Tests for the GitButler crate2nix Cargo.nix normalizer."""

from __future__ import annotations

from types import ModuleType

from lib.import_utils import load_module_from_path
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update.paths import REPO_ROOT


def _load_normalizer_module() -> ModuleType:
    module_path = REPO_ROOT / "packages" / "gitbutler" / "normalize_cargo_nix.py"
    return load_module_from_path(module_path, "_gitbutler_normalizer")


def test_normalize_disambiguates_registry_gix_trace_metadata() -> None:
    """Duplicate gix-trace sources should not collide in crate2nix target/deps."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gix-path 0.10.22" = rec {
        crateName = "gix-path";
        version = "0.10.22";
        dependencies = [
          {
            name = "gix-trace";
            packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18";
          }
        ];
      };

      "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = rec {
        crateName = "gix-trace";
        version = "0.1.18";
        edition = "2021";
        sha256 = "1q32n7l0lpa70crx3vh356l6r8s7x11q3q25d35d8dw47dj176pn";
        libName = "gix_trace";
        features = {
          "document-features" = [ "dep:document-features" ];
          "tracing" = [ "dep:tracing" ];
        };
        resolvedDefaultFeatures = [ "default" ];
      };
  };
}
"""
    expected = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gix-path 0.10.22" = rec {
        crateName = "gix-path";
        version = "0.10.22";
        dependencies = [
          {
            name = "gix-trace";
            packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18";
            features = [ "crate2nix-source-registry" ];
          }
        ];
      };

      "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = rec {
        crateName = "gix-trace";
        version = "0.1.18";
        edition = "2021";
        sha256 = "1q32n7l0lpa70crx3vh356l6r8s7x11q3q25d35d8dw47dj176pn";
        libName = "gix_trace";
        features = {
          "crate2nix-source-registry" = [ ];
          "document-features" = [ "dep:document-features" ];
          "tracing" = [ "dep:tracing" ];
        };
        resolvedDefaultFeatures = [ "crate2nix-source-registry" "default" ];
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, expected)

    normalized_again, rewrites_again, added_root_src_again = module.normalize(
        normalized
    )
    assert rewrites_again == 0
    assert added_root_src_again is False
    assert_nix_ast_equal(normalized_again, expected)


def test_normalize_disambiguates_registry_gix_validate_metadata() -> None:
    """Duplicate gix-validate sources should not collide in target/deps."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gix-path 0.11.3" = rec {
        crateName = "gix-path";
        dependencies = [
          {
            name = "gix-validate";
            packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2";
          }
        ];
      };

      "git+https://github.com/GitoxideLabs/gitoxide?rev=abc#gix-validate@0.11.2" = rec {
        crateName = "gix-validate";
        version = "0.11.2";
        libName = "gix_validate";
      };

      "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2" = rec {
        crateName = "gix-validate";
        version = "0.11.2";
        edition = "2024";
        sha256 = "1qzs9bzb0x48ggzbfr1vh9m1q9bnc3xr2yzls9yblqs03ivzrikv";
        libName = "gix_validate";
        dependencies = [
          {
            name = "bstr";
            packageId = "bstr";
          }
        ];

      };
  };
}
"""
    expected = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gix-path 0.11.3" = rec {
        crateName = "gix-path";
        dependencies = [
          {
            name = "gix-validate";
            packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2";
            features = [ "crate2nix-source-registry" ];
          }
        ];
      };

      "git+https://github.com/GitoxideLabs/gitoxide?rev=abc#gix-validate@0.11.2" = rec {
        crateName = "gix-validate";
        version = "0.11.2";
        libName = "gix_validate";
      };

      "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2" = rec {
        crateName = "gix-validate";
        version = "0.11.2";
        edition = "2024";
        sha256 = "1qzs9bzb0x48ggzbfr1vh9m1q9bnc3xr2yzls9yblqs03ivzrikv";
        libName = "gix_validate";
        dependencies = [
          {
            name = "bstr";
            packageId = "bstr";
          }
        ];

        features = {
          "crate2nix-source-registry" = [ ];
        };
        resolvedDefaultFeatures = [ "crate2nix-source-registry" ];
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, expected)

    normalized_again, rewrites_again, added_root_src_again = module.normalize(
        normalized
    )
    assert rewrites_again == 0
    assert added_root_src_again is False
    assert_nix_ast_equal(normalized_again, expected)


def test_normalize_restores_gitbutler_tauri_builtin_but_dependency() -> None:
    """The builtin-but feature should have its optional but dependency edge."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gitbutler-tauri" = rec {
        crateName = "gitbutler-tauri";
        dependencies = [
          {
            name = "anyhow";
            packageId = "anyhow";
          }
        ];
        buildDependencies = [ ];
        features = {
          "builtin-but" = [ "dep:but" "but/embedded-frontend" ];
        };
      };
  };
}
"""
    expected = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gitbutler-tauri" = rec {
        crateName = "gitbutler-tauri";
        dependencies = [
          {
            name = "but";
            packageId = "but";
            optional = true;
          }
          {
            name = "anyhow";
            packageId = "anyhow";
          }
        ];
        buildDependencies = [ ];
        features = {
          "builtin-but" = [ "dep:but" "but/embedded-frontend" ];
        };
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, expected)

    normalized_again, _rewrites_again, _added_root_src_again = module.normalize(
        normalized
    )
    assert_nix_ast_equal(normalized_again, expected)


def test_normalize_ignores_gitbutler_tauri_workspace_wrapper() -> None:
    """The optional but edge belongs on the internal crate, not its public wrapper."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  workspaceMembers = {
    "gitbutler-tauri" = rec {
      packageId = "gitbutler-tauri";
      build = internal.buildRustCrateWithFeatures {
        packageId = "gitbutler-tauri";
      };
    };
  };
  internal = {
    crates = {
      "aes" = rec {
        crateName = "aes";
        dependencies = [
          {
            name = "cfg-if";
            packageId = "cfg-if";
          }
        ];
        buildDependencies = [ ];
      };
      "gitbutler-tauri" = rec {
        crateName = "gitbutler-tauri";
        dependencies = [
          {
            name = "anyhow";
            packageId = "anyhow";
          }
        ];
        buildDependencies = [ ];
        features = {
          "builtin-but" = [ "dep:but" "but/embedded-frontend" ];
        };
      };
    };
  };
}
"""
    expected = r"""
{ rootSrc ? ./. }:
{
  workspaceMembers = {
    "gitbutler-tauri" = rec {
      packageId = "gitbutler-tauri";
      build = internal.buildRustCrateWithFeatures {
        packageId = "gitbutler-tauri";
      };
    };
  };
  internal = {
    crates = {
      "aes" = rec {
        crateName = "aes";
        dependencies = [
          {
            name = "cfg-if";
            packageId = "cfg-if";
          }
        ];
        buildDependencies = [ ];
      };
      "gitbutler-tauri" = rec {
        crateName = "gitbutler-tauri";
        dependencies = [
          {
            name = "but";
            packageId = "but";
            optional = true;
          }
          {
            name = "anyhow";
            packageId = "anyhow";
          }
        ];
        buildDependencies = [ ];
        features = {
          "builtin-but" = [ "dep:but" "but/embedded-frontend" ];
        };
      };
    };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, expected)


def test_normalize_keeps_partially_disambiguated_gix_trace_package() -> None:
    """A manually patched package should not receive a duplicate feature key."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = rec {
        crateName = "gix-trace";
        features = {
          "crate2nix-source-registry" = [ ];
        };
        resolvedDefaultFeatures = [ "default" ];
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, sample)


def test_normalize_leaves_unexpected_gix_trace_package_shape_alone() -> None:
    """Unexpected generated package shape should be left to crate2nix checks."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = rec {
        crateName = "gix-trace";
        resolvedDefaultFeatures = [ "default" ];
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, sample)


def test_normalize_leaves_gitbutler_tauri_without_dependencies_alone() -> None:
    """The optional but edge can only be inserted when dependencies exist."""
    module = _load_normalizer_module()
    sample = r"""
{ rootSrc ? ./. }:
{
  crates = {
      "gitbutler-tauri" = rec {
        crateName = "gitbutler-tauri";
        buildDependencies = [ ];
      };
  };
}
"""

    normalized, rewrites, added_root_src = module.normalize(sample)

    assert rewrites == 0
    assert added_root_src is False
    assert_nix_ast_equal(normalized, sample)
