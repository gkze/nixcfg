"""Tests for the GitButler crate2nix Cargo.nix normalizer."""

from types import ModuleType

import pytest
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.import_utils import load_module_from_path
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding, parse_nix_expr
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
          "document-features" = [ "dep:document-features" ];
          "tracing" = [ "dep:tracing" ];
          "crate2nix-source-registry" = [ ];
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


@pytest.mark.parametrize(
    "features", ['features = {"crate2nix-source-registry" = [];};', ""]
)
def test_normalize_completes_partial_source_disambiguation(features: str) -> None:
    """Incomplete prior edits must gain both the feature and its activation."""
    module = _load_normalizer_module()
    sample = (
        "{rootSrc ? ./.}: {crates = {"
        '"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = {'
        + features
        + 'resolvedDefaultFeatures = ["default"];};};}'
    )
    normalized, rewrites, added_root_src = module.normalize(sample)
    assert rewrites == 0
    assert added_root_src is False
    root = parse_nix_expr(normalized)
    assert isinstance(root, FunctionDefinition)
    assert isinstance(root.output, AttributeSet)
    crates = expect_binding(root.output.values, "crates").value
    assert isinstance(crates, AttributeSet)
    package = expect_binding(
        crates.values,
        '"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18"',
    ).value
    assert isinstance(package, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(package.values, "features").value,
        '{"crate2nix-source-registry" = [];}',
    )
    assert_nix_ast_equal(
        expect_binding(package.values, "resolvedDefaultFeatures").value,
        '["crate2nix-source-registry" "default"]',
    )
    assert_nix_ast_equal(module.normalize(normalized)[0], normalized)


@pytest.mark.parametrize(
    "graph",
    [
        "{internal = 1;}",
        "{}",
        "{crates = 1;}",
        '{crates = {"gitbutler-tauri" = {};};}',
        '{crates = {"gitbutler-tauri" = {dependencies = "wrong";};};}',
        '{crates = {"gitbutler-tauri" = {dependencies = [1];};};}',
        '{crates = {"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = 1;};}',
        '{crates = {"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = {features = 1;};};}',
        '{crates = {"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = {resolvedDefaultFeatures = 1;};};}',
        '{crates = {"registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18" = {features = {"crate2nix-source-registry" = ["unexpected"];};};};}',
        "1",
    ],
)
def test_normalize_rejects_unexpected_graph_shapes(graph: str) -> None:
    """Generator drift must fail during normalization, before a Rust build."""
    with pytest.raises((TypeError, ValueError), match="GitButler"):
        _load_normalizer_module().normalize("{rootSrc ? ./.}: " + graph)


def test_normalize_updates_every_dependency_kind_without_changing_git_source() -> None:
    """Registry identity applies to every edge and must preserve other features."""
    module = _load_normalizer_module()
    sample = r"""
    {rootSrc ? ./.}: {internal.crates = {
      inherit externalCrate;
      parent = {
        dependencies = [{packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2"; features = ["extra"]; }];
        buildDependencies = [{packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2";}];
        devDependencies = [{packageId = "git+source#gix-validate@0.11.2";}];
      };
      "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2" = {};
    };}
    """
    expected = r"""
    {rootSrc ? ./.}: {internal.crates = {
      inherit externalCrate;
      parent = {
        dependencies = [{packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2"; features = ["crate2nix-source-registry" "extra"]; }];
        buildDependencies = [{packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2"; features = ["crate2nix-source-registry"];}];
        devDependencies = [{packageId = "git+source#gix-validate@0.11.2";}];
      };
      "registry+https://github.com/rust-lang/crates.io-index#gix-validate@0.11.2" = {
        features = {"crate2nix-source-registry" = [];};
        resolvedDefaultFeatures = ["crate2nix-source-registry"];
      };
    };}
    """
    assert_nix_ast_equal(module.normalize(sample)[0], expected)


def test_normalize_rejects_a_non_function_generator_root() -> None:
    """A recognizable source marker does not authorize a malformed Cargo API."""
    with pytest.raises(ValueError, match="GitButler Cargo.nix to have a function body"):
        _load_normalizer_module().normalize("# rootSrc ? ./.\n{}")
