"""Focused tests for the Baseten CLI source updater."""

from types import ModuleType

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import load_repo_module
from lib.update.nix import _build_fetch_from_github_call


def _load_module() -> ModuleType:
    return load_repo_module(
        "packages/baseten/updater.py",
        "baseten_updater_dedicated_test",
    )


def test_source_expression_tracks_the_versioned_upstream_tag() -> None:
    """Hash the immutable source tag rather than a published binary asset."""
    module = _load_module()

    assert_nix_ast_equal(
        module.BasetenUpdater._src_expr("0.4.0"),
        _build_fetch_from_github_call(
            "basetenlabs",
            "baseten-cli",
            tag="v0.4.0",
            fetch_submodules=False,
        ),
    )


def test_updater_uses_go_vendor_hash_on_exported_systems() -> None:
    """Keep dependency hashing aligned with the package and flake systems."""
    updater = _load_module().BasetenUpdater

    assert updater.dependency_hash_type == "vendorHash"
    assert updater.supported_platforms == (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )
