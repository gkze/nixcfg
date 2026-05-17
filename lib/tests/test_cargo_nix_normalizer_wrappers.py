"""Shared contract tests for crate2nix Cargo.nix normalizer wrappers."""

from __future__ import annotations

import runpy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

import lib.cargo_nix_normalizer
from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


@dataclass(frozen=True)
class _WrapperCase:
    name: str
    script_relative: str
    cargo_relative: str

    @property
    def script_path(self) -> Path:
        return REPO_ROOT / self.script_relative


_WRAPPERS = (
    _WrapperCase(
        name="codex",
        script_relative="packages/codex/normalize_cargo_nix.py",
        cargo_relative="packages/codex/Cargo.nix",
    ),
    _WrapperCase(
        name="gitbutler",
        script_relative="packages/gitbutler/normalize_cargo_nix.py",
        cargo_relative="packages/gitbutler/Cargo.nix",
    ),
    _WrapperCase(
        name="goose",
        script_relative="overlays/goose-cli/normalize_cargo_nix.py",
        cargo_relative="overlays/goose-cli/Cargo.nix",
    ),
    _WrapperCase(
        name="zed",
        script_relative="packages/zed-editor-nightly/normalize_cargo_nix.py",
        cargo_relative="packages/zed-editor-nightly/Cargo.nix",
    ),
)
_NOOP_WRAPPERS = tuple(
    case for case in _WRAPPERS if case.name in {"codex", "goose", "zed"}
)


def _load_normalizer_module(case: _WrapperCase) -> ModuleType:
    return load_module_from_path(case.script_path, f"_{case.name}_normalizer")


@pytest.mark.parametrize("case", _NOOP_WRAPPERS, ids=lambda case: case.name)
def test_wrapper_normalize_is_noop_for_checked_in_cargo_nix(
    case: _WrapperCase,
) -> None:
    """Checked-in generated Cargo.nix files should already be wrapper-normalized."""
    module = _load_normalizer_module(case)
    cargo_nix = REPO_ROOT / case.cargo_relative

    original = cargo_nix.read_text()
    normalized, rewrites, added_root_src = module.normalize(original)

    assert added_root_src is False
    assert rewrites == 0

    _normalized_again, rewrites_again, added_root_src_again = module.normalize(
        normalized
    )

    assert added_root_src_again is False
    assert rewrites_again == 0


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_bootstrap_repo_import_path_prefers_env_root(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct wrapper execution should honor an explicit REPO_ROOT override."""
    module = _load_normalizer_module(case)
    monkeypatch.setenv("REPO_ROOT", f"/tmp/{case.name}-root")
    monkeypatch.setattr(module.sys, "path", [])

    module._bootstrap_repo_import_path()

    assert module.sys.path == [str(Path(f"/tmp/{case.name}-root").resolve())]


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_bootstrap_repo_import_path_finds_root_marker_and_errors_when_missing(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Wrapper repo-root discovery should walk candidates and fail clearly."""
    module = _load_normalizer_module(case)
    repo_root = tmp_path / "repo"
    nested = repo_root / Path(case.script_relative).parent
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


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_resolve_path_handles_relative_and_absolute_paths(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper CLI path resolution should keep absolutes and anchor relatives."""
    module = _load_normalizer_module(case)
    monkeypatch.setattr(module, "get_repo_root", lambda: Path("/repo"))

    assert module._resolve_path("/tmp/Cargo.nix") == Path("/tmp/Cargo.nix")
    assert module._resolve_path(case.cargo_relative) == Path(
        f"/repo/{case.cargo_relative}"
    )


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_main_updates_file_and_reports_status(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wrapper main should rewrite the target file when normalization changes it."""
    module = _load_normalizer_module(case)
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


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_main_uses_default_path_and_reports_no_change(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wrapper main should default to the checked-in Cargo.nix without no-op writes."""
    module = _load_normalizer_module(case)
    repo_root = tmp_path / "repo"
    cargo_nix = repo_root / case.cargo_relative
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


@pytest.mark.parametrize("case", _WRAPPERS, ids=lambda case: case.name)
def test_wrapper_main_guard_exits_with_main_result(
    case: _WrapperCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Executing a wrapper as __main__ should raise SystemExit(main())."""
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("stable", encoding="utf-8")

    monkeypatch.setenv("REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setattr(
        lib.cargo_nix_normalizer,
        "normalize",
        lambda text, **_kwargs: (text, 0, False),
    )
    monkeypatch.setattr("sys.argv", [str(case.script_path), str(cargo_nix)])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(case.script_path), run_name="__main__")

    assert excinfo.value.code == 0
