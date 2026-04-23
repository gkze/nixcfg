"""Tests for the opencode-desktop crate2nix Cargo.nix normalizer."""

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
    module_path = REPO_ROOT / "packages" / "opencode-desktop" / "normalize_cargo_nix.py"
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
    cargo_nix = REPO_ROOT / "packages" / "opencode-desktop" / "Cargo.nix"

    original = cargo_nix.read_text()
    normalized, rewrites, added_root_src = module.normalize(original)

    assert added_root_src is False
    assert rewrites == 0

    _normalized_again, rewrites_again, added_root_src_again = module.normalize(
        normalized
    )

    assert added_root_src_again is False
    assert rewrites_again == 0


def test_bootstrap_repo_import_path_prefers_env_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct execution should honor an explicit REPO_ROOT override."""
    module = _load_normalizer_module()
    monkeypatch.setenv("REPO_ROOT", "/tmp/opencode-root")
    monkeypatch.setattr(module.sys, "path", [])

    module._bootstrap_repo_import_path()

    assert module.sys.path == [str(Path("/tmp/opencode-root").resolve())]


def test_bootstrap_repo_import_path_finds_root_marker_and_errors_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repo-root discovery should walk candidate directories and fail clearly otherwise."""
    module = _load_normalizer_module()
    repo_root = tmp_path / "repo"
    nested = repo_root / "packages" / "opencode-desktop"
    nested.mkdir(parents=True)
    (repo_root / ".root").write_text("\n", encoding="utf-8")

    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda _cls: nested))
    monkeypatch.setattr(module, "__file__", str(nested / "normalize_cargo_nix.py"))
    monkeypatch.setattr(module.sys, "path", [])

    module._bootstrap_repo_import_path()

    assert module.sys.path == [str(repo_root)]

    missing_root = tmp_path / "missing"
    monkeypatch.setattr(module.Path, "cwd", classmethod(lambda _cls: missing_root))
    monkeypatch.setattr(
        module, "__file__", str(missing_root / "normalize_cargo_nix.py")
    )
    monkeypatch.setattr(module.sys, "path", [])

    with pytest.raises(RuntimeError, match="Could not find repo root"):
        module._bootstrap_repo_import_path()


def test_resolve_path_handles_relative_and_absolute_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI path resolution should keep absolutes and anchor relatives to the repo."""
    module = _load_normalizer_module()
    monkeypatch.setattr(module, "get_repo_root", lambda: Path("/repo"))

    assert module._resolve_path("/tmp/Cargo.nix") == Path("/tmp/Cargo.nix")
    assert module._resolve_path("packages/opencode-desktop/Cargo.nix") == Path(
        "/repo/packages/opencode-desktop/Cargo.nix"
    )


def test_main_updates_file_and_reports_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Main should rewrite the target file in place when normalization changes it."""
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
    """Main should default to the checked-in Cargo.nix path and avoid rewriting identical text."""
    module = _load_normalizer_module()
    repo_root = tmp_path / "repo"
    cargo_nix = repo_root / "packages" / "opencode-desktop" / "Cargo.nix"
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
    repo_root = tmp_path / "repo"
    cargo_nix = repo_root / "packages" / "opencode-desktop" / "Cargo.nix"
    cargo_nix.parent.mkdir(parents=True)
    cargo_nix.write_text("stable", encoding="utf-8")

    script_path = REPO_ROOT / "packages/opencode-desktop/normalize_cargo_nix.py"

    monkeypatch.setenv("REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setattr(
        lib.cargo_nix_normalizer,
        "normalize",
        lambda text, **_kwargs: (text, 0, False),
    )
    monkeypatch.setattr(
        "sys.argv",
        [str(script_path), str(cargo_nix)],
    )

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(
            str(script_path),
            run_name="__main__",
        )

    assert excinfo.value.code == 0
