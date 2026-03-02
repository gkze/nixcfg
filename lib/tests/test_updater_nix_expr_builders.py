"""Tests for updater-specific Nix expression builders."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from nix_manipulator import parse

from lib.tests._assertions import check
from packages.scratch.updater import ScratchUpdater

if TYPE_CHECKING:
    from types import ModuleType


def _load_sentry_cli_updater_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[2] / "overlays" / "sentry-cli" / "updater.py"
    )
    spec = importlib.util.spec_from_file_location("_sentry_cli_updater", module_path)
    if spec is None or spec.loader is None:
        msg = f"Could not load sentry-cli updater module from {module_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scratch_npm_expr_is_parseable() -> None:
    """Scratch NPM hash expression should be valid Nix."""
    expr = object.__getattribute__(ScratchUpdater, "_expr_for_npm_deps")()

    parse(expr)
    check("pkgs.fetchNpmDeps" in expr)
    check("hash = pkgs.lib.fakeHash;" in expr)


def test_scratch_cargo_expr_is_parseable() -> None:
    """Scratch cargo hash expression should be valid Nix."""
    expr = object.__getattribute__(ScratchUpdater, "_expr_for_cargo_vendor")()

    parse(expr)
    check("pkgs.rustPlatform.fetchCargoVendor" in expr)
    check("src-tauri" in expr)


def test_sentry_src_expr_is_parseable() -> None:
    """Sentry source hash expression should be valid Nix."""
    module = _load_sentry_cli_updater_module()
    updater = module.SentryCliUpdater()

    expr = object.__getattribute__(updater, "_src_nix_expr")("v2.0.0")

    parse(expr)
    check("pkgs.fetchFromGitHub" in expr)
    check("hash = pkgs.lib.fakeHash;" in expr)


def test_sentry_cargo_expr_is_parseable() -> None:
    """Sentry cargo hash expression should be valid Nix."""
    module = _load_sentry_cli_updater_module()
    updater = module.SentryCliUpdater()

    expr = object.__getattribute__(updater, "_cargo_nix_expr")(
        "v2.0.0", "sha256-someHashValue="
    )

    parse(expr)
    check("pkgs.rustPlatform.fetchCargoVendor" in expr)
    check("hash = pkgs.lib.fakeHash;" in expr)
    check('hash = "sha256-someHashValue=";' in expr)
