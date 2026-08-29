"""Tests for per-package path discovery helpers."""

from typing import TYPE_CHECKING

import pytest

from lib.update.paths import (
    local_flake_url,
    package_dir_for_in,
    package_file_map_in,
    package_file_names_in,
    updater_dir_for,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_package_file_map_in_supports_dir_and_flat_layouts(tmp_path: Path) -> None:
    """Discover files from both <name>/<file> and <name>.<file> layouts."""
    dir_file = tmp_path / "packages" / "alpha" / "sources.json"
    dir_file.parent.mkdir(parents=True, exist_ok=True)
    dir_file.write_text("{}\n", encoding="utf-8")

    flat_file = tmp_path / "overlays" / "beta.sources.json"
    flat_file.parent.mkdir(parents=True, exist_ok=True)
    flat_file.write_text("{}\n", encoding="utf-8")

    discovered = package_file_map_in(tmp_path, "sources.json")

    assert set(discovered) == {"alpha", "beta"}
    assert discovered["alpha"] == dir_file
    assert discovered["beta"] == flat_file


def test_package_file_map_in_rejects_duplicate_names(tmp_path: Path) -> None:
    """Fail when directory and flat layouts define the same package name."""
    dir_file = tmp_path / "packages" / "demo" / "sources.json"
    dir_file.parent.mkdir(parents=True, exist_ok=True)
    dir_file.write_text("{}\n", encoding="utf-8")

    flat_file = tmp_path / "packages" / "demo.sources.json"
    flat_file.parent.mkdir(parents=True, exist_ok=True)
    flat_file.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="demo"):
        package_file_map_in(tmp_path, "sources.json")


def test_package_file_names_in_allows_duplicate_names(tmp_path: Path) -> None:
    """Return logical names when duplicate package locations are acceptable."""
    dir_file = tmp_path / "packages" / "demo" / "default.nix"
    dir_file.parent.mkdir(parents=True, exist_ok=True)
    dir_file.write_text("{}\n", encoding="utf-8")

    overlay_file = tmp_path / "overlays" / "demo" / "default.nix"
    overlay_file.parent.mkdir(parents=True, exist_ok=True)
    overlay_file.write_text("{}\n", encoding="utf-8")

    assert package_file_names_in(tmp_path, "default.nix") == {"demo"}


def test_package_dir_for_in_returns_unique_match(tmp_path: Path) -> None:
    """Resolve a unique package directory under an arbitrary root."""
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True, exist_ok=True)

    assert package_dir_for_in(tmp_path, "demo") == package_dir


def test_package_dir_for_in_rejects_duplicate_package_dirs(tmp_path: Path) -> None:
    """Fail when package and overlay directories share the same package name."""
    (tmp_path / "packages" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "overlays" / "demo").mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="Duplicate package directories"):
        package_dir_for_in(tmp_path, "demo")


def test_updater_dir_for_uses_the_authoritative_directory_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve updater ownership despite a same-named shadow directory."""
    owner = tmp_path / "overlays" / "demo"
    owner.mkdir(parents=True)
    (tmp_path / "packages" / "demo").mkdir(parents=True)
    (owner / "updater.py").write_text("# updater\n", encoding="utf-8")
    flat = tmp_path / "overlays" / "flat.updater.py"
    flat.write_text("# updater\n", encoding="utf-8")

    monkeypatch.setattr("lib.update.paths.get_repo_root", lambda: tmp_path)
    assert updater_dir_for("demo") == owner
    assert updater_dir_for("flat") is None
    assert updater_dir_for("missing") is None


def test_local_flake_url_uses_git_source_view(tmp_path: Path) -> None:
    """Local update-time flake reads should avoid raw path copies of .git."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert local_flake_url(repo_root) == f"git+file://{repo_root}?dirty=1"
