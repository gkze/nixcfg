"""Keep updater platform declarations aligned with persisted source metadata."""

from __future__ import annotations

import json
from functools import cache

import pytest
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet
from nix_manipulator.expressions.with_statement import WithStatement

from lib.import_utils import load_module_from_path
from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@pytest.mark.parametrize(
    ("source_rel", "updater_rel", "class_name"),
    [
        (
            "packages/sculptor/sources.json",
            "packages/sculptor/updater.py",
            "SculptorUpdater",
        ),
        (
            "overlays/datagrip/sources.json",
            "overlays/datagrip/updater.py",
            "DataGripUpdater",
        ),
        (
            "overlays/google-chrome/sources.json",
            "overlays/google-chrome/updater.py",
            "GoogleChromeUpdater",
        ),
        (
            "overlays/vscode-insiders/sources.json",
            "overlays/vscode-insiders/updater.py",
            "VSCodeInsidersUpdater",
        ),
        (
            "overlays/zoom-us/sources.json",
            "overlays/zoom-us/updater.py",
            "ZoomUsUpdater",
        ),
    ],
)
def test_sources_json_urls_cover_each_updater_platform(
    source_rel: str,
    updater_rel: str,
    class_name: str,
) -> None:
    """Every declared updater platform should be persisted in sources.json."""
    payload = json.loads((REPO_ROOT / source_rel).read_text(encoding="utf-8"))
    urls = payload.get("urls")
    assert isinstance(urls, dict)

    module_name = updater_rel.replace("/", "_").replace(".", "_")
    updater_module = load_module_from_path(REPO_ROOT / updater_rel, module_name)
    updater_cls = getattr(updater_module, class_name)

    assert sorted(urls) == sorted(updater_cls.PLATFORMS)


@cache
def _sculptor_platform_switch() -> IfExpression:
    """Return the top-level platform switch for the Sculptor package."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/sculptor/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, IfExpression)


@cache
def _emdash_derivation() -> FunctionCall:
    """Return the top-level Emdash derivation call."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/emdash/default.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, FunctionCall)


def test_sculptor_package_meta_platforms_match_its_source_matrix() -> None:
    """Sculptor package metadata should advertise every supported source artifact."""
    meta = expect_instance(
        expect_scope_binding(_sculptor_platform_switch(), "meta").value,
        WithStatement,
    )
    meta_body = expect_instance(meta.body, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(meta_body.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-darwin" "x86_64-linux" ]',
    )


def test_emdash_electron_platform_maps_cover_intel_mac() -> None:
    """Emdash should keep its Electron target/hash maps in sync for Intel macOS."""
    expected = {
        "aarch64-darwin",
        "x86_64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    }
    electron_targets = expect_instance(
        expect_scope_binding(_emdash_derivation(), "electronTargets").value,
        AttributeSet,
    )
    electron_hashes = expect_instance(
        expect_scope_binding(_emdash_derivation(), "electronZipHashes").value,
        AttributeSet,
    )

    assert {
        binding.name for binding in electron_targets.values if hasattr(binding, "name")
    } == expected
    assert {
        binding.name for binding in electron_hashes.values if hasattr(binding, "name")
    } == expected
