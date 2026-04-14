"""Source-level guardrails for Zed nightly packaging and wiring."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from lib.update.paths import REPO_ROOT


@cache
def _zed_package_text() -> str:
    """Return Zed nightly's package expression source text."""
    return Path(REPO_ROOT / "packages/zed-editor-nightly/default.nix").read_text(
        encoding="utf-8"
    )


@cache
def _home_configuration_text() -> str:
    """Return George's standalone Home Manager configuration text."""
    return Path(REPO_ROOT / "home/george/configuration.nix").read_text(encoding="utf-8")


@cache
def _zed_module_text() -> str:
    """Return George's host-only Zed Home Manager module text."""
    return Path(REPO_ROOT / "home/george/zed.nix").read_text(encoding="utf-8")


@cache
def _darwin_host_text(host: str) -> str:
    """Return one Darwin host entrypoint's source text."""
    return Path(REPO_ROOT / f"darwin/{host}.nix").read_text(encoding="utf-8")


def test_zed_fontconfig_uses_raw_source_paths() -> None:
    """Avoid forcing the Darwin-only patched source derivation during eval."""
    source = _zed_package_text()

    assert '"${src}/assets/fonts/lilex"' in source
    assert '"${src}/assets/fonts/ibm-plex-sans"' in source
    assert '"${patchedSrc}/assets/fonts/lilex"' not in source
    assert '"${patchedSrc}/assets/fonts/ibm-plex-sans"' not in source


def test_standalone_home_config_does_not_reference_zed_package() -> None:
    """Keep the standalone Darwin home config safe for Linux CI evals."""
    source = _home_configuration_text()

    assert "pkgs.zed-editor-nightly" not in source
    assert "programs.zed-editor" not in source


def test_host_only_zed_module_keeps_nightly_package_wiring() -> None:
    """Darwin hosts should still install and configure the nightly editor."""
    source = _zed_module_text()

    assert "programs.zed-editor = {" in source
    assert "package = pkgs.zed-editor-nightly;" in source


def test_darwin_hosts_import_the_host_only_zed_module() -> None:
    """Argus and Rocinante should keep the shared Zed host module enabled."""
    assert "../home/george/zed.nix" in _darwin_host_text("argus")
    assert "../home/george/zed.nix" in _darwin_host_text("rocinante")
