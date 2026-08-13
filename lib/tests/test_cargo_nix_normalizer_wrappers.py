"""Contract tests for package-specific crate2nix normalizer callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from lib.import_utils import load_module_from_path
from lib.update import crate2nix
from lib.update.ci import crate2nix as crate2nix_cli
from lib.update.paths import REPO_ROOT


@dataclass(frozen=True)
class _NormalizerCase:
    name: str
    module_relative: str
    cargo_relative: str

    @property
    def module_path(self) -> Path:
        return REPO_ROOT / self.module_relative


_NORMALIZERS = (
    _NormalizerCase(
        name="codex",
        module_relative="packages/codex/normalize_cargo_nix.py",
        cargo_relative="packages/codex/Cargo.nix",
    ),
    _NormalizerCase(
        name="gitbutler",
        module_relative="packages/gitbutler/normalize_cargo_nix.py",
        cargo_relative="packages/gitbutler/Cargo.nix",
    ),
    _NormalizerCase(
        name="goose-cli",
        module_relative="overlays/goose-cli/normalize_cargo_nix.py",
        cargo_relative="overlays/goose-cli/Cargo.nix",
    ),
    _NormalizerCase(
        name="zed-editor-nightly",
        module_relative="packages/zed-editor-nightly/normalize_cargo_nix.py",
        cargo_relative="packages/zed-editor-nightly/Cargo.nix",
    ),
)


def test_crate2nix_cli_exports_only_entrypoints() -> None:
    """Keep regeneration internals behind the core crate2nix module boundary."""
    assert crate2nix_cli.__all__ == ["app", "main"]


def test_crate2nix_cli_forwards_regeneration_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward operator selections to the core regeneration command."""
    called: dict[str, object] = {}

    def _run(*, packages: tuple[str, ...] = (), write: bool = False) -> int:
        called.update(packages=packages, write=write)
        return 7

    monkeypatch.setattr(crate2nix, "run", _run)

    assert crate2nix_cli.main(["--package", "demo", "--write"]) == 7
    assert called == {"packages": ("demo",), "write": True}


def _load_normalizer(case: _NormalizerCase) -> ModuleType:
    return load_module_from_path(
        case.module_path,
        f"_{case.name.replace('-', '_')}_normalizer_contract",
    )


@pytest.mark.parametrize("case", _NORMALIZERS, ids=lambda case: case.name)
def test_checked_in_cargo_nix_is_normalized(case: _NormalizerCase) -> None:
    """Keep package callbacks pure and idempotent on checked-in artifacts."""
    module = _load_normalizer(case)
    original = (REPO_ROOT / case.cargo_relative).read_text(encoding="utf-8")

    normalized, rewrites, added_root_src = module.normalize(original)

    assert normalized == original
    assert rewrites == 0
    assert added_root_src is False


def test_central_normalizer_command_updates_an_explicit_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize through the crate2nix CLI instead of package-local scripts."""
    normalizer = tmp_path / "normalize.py"
    normalizer.write_text(
        "def normalize(text: str) -> tuple[str, int, bool]:\n"
        '    return (text.replace("old", "new"), 1, True)\n',
        encoding="utf-8",
    )
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("old\n", encoding="utf-8")
    monkeypatch.setitem(
        crate2nix.TARGETS,
        "demo",
        crate2nix.Crate2NixTarget(
            name="demo",
            patched_src_installable="path:.#demo-crate2nix-src",
            cargo_nix=cargo_nix,
            crate_hashes=tmp_path / "crate-hashes.json",
            normalizer_path=normalizer,
            supported_platforms=("test",),
        ),
    )

    result = CliRunner().invoke(
        crate2nix_cli.app,
        ["normalize", "demo", str(cargo_nix)],
    )

    assert result.exit_code == 0
    assert cargo_nix.read_text(encoding="utf-8") == "new\n"
    assert result.output == (
        f"{cargo_nix}: added rootSrc, rewrote 1 source path(s), updated file\n"
    )


def test_central_normalizer_command_rejects_unknown_target() -> None:
    """Report the registered target names for operator mistakes."""
    result = CliRunner().invoke(
        crate2nix_cli.app,
        ["normalize", "missing-target"],
    )

    assert result.exit_code != 0
    assert "Unknown crate2nix target" in result.output
    assert "codex" in result.output


def test_central_normalizer_command_uses_registered_default_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve a target's repo-relative Cargo.nix and avoid no-op writes."""
    normalizer = tmp_path / "normalize.py"
    normalizer.write_text(
        "def normalize(text: str) -> tuple[str, int, bool]:\n"
        "    return (text, 0, False)\n",
        encoding="utf-8",
    )
    cargo_nix = tmp_path / "Cargo.nix"
    cargo_nix.write_text("stable\n", encoding="utf-8")
    monkeypatch.setitem(
        crate2nix.TARGETS,
        "demo-default",
        crate2nix.Crate2NixTarget(
            name="demo-default",
            patched_src_installable="path:.#demo-default-crate2nix-src",
            cargo_nix=Path("Cargo.nix"),
            crate_hashes=Path("crate-hashes.json"),
            normalizer_path=normalizer,
            supported_platforms=("test",),
        ),
    )
    monkeypatch.setattr(crate2nix, "REPO_ROOT", tmp_path)

    result = CliRunner().invoke(
        crate2nix_cli.app,
        ["normalize", "demo-default"],
    )

    assert result.exit_code == 0
    assert cargo_nix.read_text(encoding="utf-8") == "stable\n"
    assert result.output == (
        f"{cargo_nix}: rootSrc already present, rewrote 0 source path(s), "
        "no content change\n"
    )
