"""Tests for the GitButler crate2nix Cargo.nix normalizer."""

from __future__ import annotations

import runpy
from pathlib import Path
from types import ModuleType

import pytest

import lib.cargo_nix_normalizer
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


def test_bootstrap_repo_import_path_handles_env_marker_and_missing_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repo-root discovery should honor REPO_ROOT, root markers, and errors."""
    module = _load_normalizer_module()
    repo_root = tmp_path / "repo"
    nested = repo_root / "packages" / "gitbutler"
    nested.mkdir(parents=True)
    (repo_root / ".root").write_text("\n", encoding="utf-8")

    monkeypatch.setenv("REPO_ROOT", str(repo_root))
    monkeypatch.setattr(module.sys, "path", [])
    module._bootstrap_repo_import_path()
    assert module.sys.path == [str(repo_root)]

    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda _cls: nested))
    monkeypatch.setattr(module, "__file__", str(nested / "normalize_cargo_nix.py"))
    monkeypatch.setattr(module.sys, "path", [])
    module._bootstrap_repo_import_path()
    assert module.sys.path == [str(repo_root)]

    missing_root = tmp_path / "missing"
    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda _cls: missing_root))
    monkeypatch.setattr(
        module,
        "__file__",
        str(missing_root / "normalize_cargo_nix.py"),
    )
    monkeypatch.setattr(module.sys, "path", [])

    with pytest.raises(RuntimeError, match="Could not find repo root"):
        module._bootstrap_repo_import_path()


def test_resolve_path_handles_relative_and_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI path resolution should keep absolutes and anchor relatives."""
    module = _load_normalizer_module()
    monkeypatch.setattr(module, "get_repo_root", lambda: Path("/repo"))

    assert module._resolve_path("/tmp/Cargo.nix") == Path("/tmp/Cargo.nix")
    assert module._resolve_path("packages/gitbutler/Cargo.nix") == Path(
        "/repo/packages/gitbutler/Cargo.nix"
    )


def test_main_updates_file_and_reports_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main should rewrite the target file when normalization changes it."""
    module = _load_normalizer_module()
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("original", encoding="utf-8")
    monkeypatch.setattr(module.sys, "argv", ["normalize_cargo_nix.py", str(cargo_nix)])
    monkeypatch.setattr(
        module,
        "normalize",
        lambda text: (
            ("normalized", 2, True) if text == "original" else (text, 0, False)
        ),
    )

    assert module.main() == 0
    assert cargo_nix.read_text(encoding="utf-8") == "normalized"
    assert capsys.readouterr().out == (
        f"{cargo_nix}: added rootSrc, rewrote 2 source path(s), updated file\n"
    )


def test_main_uses_default_path_and_reports_no_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main should default to GitButler Cargo.nix and avoid no-op writes."""
    module = _load_normalizer_module()
    repo_root = tmp_path / "repo"
    cargo_nix = repo_root / "packages" / "gitbutler" / "Cargo.nix"
    cargo_nix.parent.mkdir(parents=True)
    cargo_nix.write_text("stable", encoding="utf-8")

    monkeypatch.setattr(module, "get_repo_root", lambda: repo_root)
    monkeypatch.setattr(module.sys, "argv", ["normalize_cargo_nix.py"])
    monkeypatch.setattr(module, "normalize", lambda text: (text, 0, False))

    assert module.main() == 0
    assert cargo_nix.read_text(encoding="utf-8") == "stable"
    assert capsys.readouterr().out == (
        f"{cargo_nix}: rootSrc already present, rewrote 0 source path(s), no content change\n"
    )


def test_main_guard_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing the helper as __main__ should raise SystemExit(main())."""
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("stable", encoding="utf-8")
    script_path = REPO_ROOT / "packages/gitbutler/normalize_cargo_nix.py"

    monkeypatch.setenv("REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setattr(
        lib.cargo_nix_normalizer,
        "normalize",
        lambda text, **_kwargs: (text, 0, False),
    )
    monkeypatch.setattr("sys.argv", [str(script_path), str(cargo_nix)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script_path), run_name="__main__")

    assert excinfo.value.code == 0
