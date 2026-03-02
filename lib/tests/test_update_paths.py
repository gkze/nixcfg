"""Tests for per-package path discovery helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from lib.tests._assertions import check
from lib.update.paths import (
    SOURCES_GIT_PATHSPECS,
    is_sources_file_path,
    package_dir_for_in,
    package_file_map_in,
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

    check(set(discovered) == {"alpha", "beta"})
    check(discovered["alpha"] == dir_file)
    check(discovered["beta"] == flat_file)


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


def test_sources_git_pathspecs_cover_directory_and_flat_layouts() -> None:
    """Emit git pathspecs for both supported per-package sources layouts."""
    check(
        SOURCES_GIT_PATHSPECS
        == (
            ":(glob)packages/**/sources.json",
            ":(glob)packages/*.sources.json",
            ":(glob)overlays/**/sources.json",
            ":(glob)overlays/*.sources.json",
        )
    )


def test_is_sources_file_path_matches_supported_layouts() -> None:
    """Recognize per-package sources paths under packages and overlays."""
    check(is_sources_file_path("packages/demo/sources.json"))
    check(is_sources_file_path("packages/demo.sources.json"))
    check(is_sources_file_path("overlays/demo/sources.json"))
    check(is_sources_file_path("overlays/demo.sources.json"))

    check(not is_sources_file_path("misc/demo/sources.json"))
    check(not is_sources_file_path("packages/demo/default.nix"))


def test_package_dir_for_in_returns_unique_match(tmp_path: Path) -> None:
    """Resolve a unique package directory under an arbitrary root."""
    package_dir = tmp_path / "packages" / "demo"
    package_dir.mkdir(parents=True, exist_ok=True)

    check(package_dir_for_in(tmp_path, "demo") == package_dir)


def test_package_dir_for_in_rejects_duplicate_package_dirs(tmp_path: Path) -> None:
    """Fail when package and overlay directories share the same package name."""
    (tmp_path / "packages" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "overlays" / "demo").mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="Duplicate package directories"):
        package_dir_for_in(tmp_path, "demo")
