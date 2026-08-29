"""Behavioral coverage for updater package probes that require selfSource."""

import shutil

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._nix_ast import parse_nix_expr
from lib.tests._nix_eval import nix_eval_raw
from lib.update.nix import _build_package_path_attr_expr


@pytest.mark.skipif(shutil.which("nix") is None, reason="nix command not available")
def test_package_probe_injects_overridden_self_source() -> None:
    """Evaluate the callPackage boundary because ASTs cannot prove argument injection."""
    override = SourceEntry.model_validate({
        "version": "9.9.9-test",
        "hashes": {
            "aarch64-darwin": ("sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        },
        "urls": {
            "aarch64-darwin": "https://example.invalid/Wispr-Flow.dmg",
        },
    })

    expression = parse_nix_expr(
        _build_package_path_attr_expr(
            "wispr-flow",
            ".version",
            system="aarch64-darwin",
            source_overrides={"wispr-flow": override},
        )
    )

    assert nix_eval_raw(expression) == "9.9.9-test"
