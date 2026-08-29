"""Semantic contracts for the first source-backed macOS app migrations."""

from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.ellipses import Ellipses
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.identifier import Identifier
from nix_manipulator.expressions.list import NixList
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    binding_map,
    expect_binding,
    expect_scope_binding,
)
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr
from lib.tests._updater_helpers import load_repo_module, run_async
from lib.update.updaters import VersionInfo
from lib.update.updaters.metadata import AssetURLsMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

_AGENTLOG_VERSION = "0.4.2"
_AGENTLOG_ASSET_NAME = "Agentlog_0.4.2_aarch64.dmg"
_AGENTLOG_URL = (
    "https://github.com/jordienr/agentlog-releases/releases/download/"
    f"v{_AGENTLOG_VERSION}/{_AGENTLOG_ASSET_NAME}"
)
_AGENTLOG_HASH = "sha256-X12FJkdkstnJBfixcohsEiEsoUK1l4QUkiWwEYBMcJI="


def _load_agentlog_updater() -> ModuleType:
    return load_repo_module(
        "packages/agentlog/updater.py",
        "agentlog_updater_contract_test",
    )


def test_agentlog_updater_pins_the_official_arm64_release_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agentlog updates should retain the immutable, architecture-specific DMG."""
    module = _load_agentlog_updater()
    updater = module.AgentlogUpdater()

    async def _fetch_release(
        _session: object,
        endpoint: str,
        *,
        config: object,
    ) -> Mapping[str, object]:
        assert endpoint == "repos/jordienr/agentlog-releases/releases/latest"
        assert config is updater.config
        return {
            "tag_name": f"v{_AGENTLOG_VERSION}",
            "assets": [
                {
                    "name": _AGENTLOG_ASSET_NAME,
                    "browser_download_url": _AGENTLOG_URL,
                }
            ],
        }

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_release,
    )

    info = run_async(updater.fetch_latest(object()))
    result = updater.build_result(info, {"aarch64-darwin": _AGENTLOG_HASH})

    assert updater.PLATFORMS == {"aarch64-darwin": "aarch64"}
    assert info == VersionInfo(
        version=_AGENTLOG_VERSION,
        metadata=AssetURLsMetadata({"aarch64-darwin": _AGENTLOG_URL}),
    )
    assert result.urls == {"aarch64-darwin": _AGENTLOG_URL}
    assert result.hashes.to_json() == {"aarch64-darwin": _AGENTLOG_HASH}


def test_agentlog_package_uses_the_shared_copy_mode_dmg_contract() -> None:
    """Agentlog should inherit copy-mode macApp metadata from the shared helper."""
    package = expect_instance(
        nix_file_expr("packages/agentlog/default.nix"),
        FunctionDefinition,
    )
    derivation = expect_instance(package.output, FunctionCall)
    attrs = expect_instance(derivation.argument, AttributeSet)

    assert_nix_ast_equal(derivation.name, Identifier(name="mkSimpleDarwinApp"))
    assert_nix_ast_equal(
        expect_binding(attrs.values, "builder").value,
        Identifier(name="mkDmgApp"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "pname").value,
        StringPrimitive(value="agentlog"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "appName").value,
        StringPrimitive(value="Agentlog"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "executableName").value,
        StringPrimitive(value="agentlog"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "info").value,
        Identifier(name="selfSource"),
    )
    assert_nix_ast_equal(
        expect_binding(attrs.values, "platforms").value,
        '[ "aarch64-darwin" ]',
    )
    assert "macApp" not in binding_map(attrs.values)


def test_baseten_switch_separates_the_cli_from_its_managed_app() -> None:
    """The app route should expose one app-free CLI package to Home Manager."""
    package = expect_instance(
        nix_file_expr("packages/baseten-switch/default.nix"),
        FunctionDefinition,
    )
    derivation = expect_instance(
        expect_scope_binding(package.output, "package").value,
        FunctionCall,
    )
    attrs = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(attrs.values, "passthru").value,
        AttributeSet,
    )

    assert_nix_ast_equal(derivation.name, Identifier(name="buildGoModule"))
    cli_package = expect_binding(passthru.values, "cliPackage").value
    assert_nix_ast_equal(
        "{ runCommand, pname, version, meta, package }: " + cli_package.rebuild(),
        """
        { runCommand, pname, version, meta, package }:
        runCommand "${pname}-cli-${version}" { inherit meta; } ''
          mkdir -p "$out/bin"
          ln -s "${package}/Applications/Baseten Switch.app/Contents/Resources/baseten-switch" "$out/bin/baseten-switch"
        ''
        """,
    )
    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        """
        {
          bundleName = "Baseten Switch.app";
          bundleRelPath = "Applications/Baseten Switch.app";
          installMode = "copy";
        }
        """,
    )

    work = expect_instance(
        nix_file_expr("home/george/work.nix"),
        FunctionDefinition,
    )
    work_output = expect_instance(work.output, AttributeSet)
    nixcfg = expect_instance(
        expect_binding(work_output.values, "nixcfg").value,
        AttributeSet,
    )
    package_sets = expect_instance(
        expect_binding(nixcfg.values, "packageSets").value,
        AttributeSet,
    )
    extra_packages = expect_instance(
        expect_binding(package_sets.values, "extraPackages").value,
        NixList,
    )
    assert_nix_ast_equal(
        extra_packages,
        """
        [
          pkgs.baseten
          pkgs.baseten-switch.cliPackage
          pkgs.executor.cliPackage
          pkgs.pants-preview
          pkgs.traycer.cliPackage
          pkgs.writer-computer.cliPackage
        ]
        """,
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "packages/bb/default.nix",
        "packages/clearly/default.nix",
    ],
)
def test_source_built_apps_expose_copy_mode_mac_app_metadata(
    relative_path: str,
) -> None:
    """Custom app derivations should remain routable without bespoke activation."""
    package = expect_instance(nix_file_expr(relative_path), FunctionDefinition)
    output = package.output
    if isinstance(output, Assertion):
        derivation = expect_instance(output.body, FunctionCall)
    else:
        derivation = expect_instance(output, FunctionCall)
    attrs = expect_instance(derivation.argument, AttributeSet)
    passthru = expect_instance(
        expect_binding(attrs.values, "passthru").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        expect_binding(passthru.values, "macApp").value,
        """
        {
          bundleName = appBundleName;
          bundleRelPath = "Applications/${appBundleName}";
          installMode = "copy";
        }
        """,
    )


@pytest.mark.parametrize(
    "package_name",
    [
        "agentlog",
        "baseten-switch",
        "bb",
        "clearly",
        "executor",
        "gooeypi",
        "hermes-desktop",
        "openchamber",
        "paseo",
        "reflect-open",
        "unsloth",
        "waku",
        "writer-computer",
        "zeron",
    ],
)
def test_binary_darwin_overlay_exports_migrated_app(
    package_name: str,
) -> None:
    """Each migrated app must remain reachable from the shared Darwin overlay."""
    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    exports = expect_instance(overlay.output, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(exports.values, package_name).value,
        f'callDarwinAppPackage "{package_name}"',
    )


@pytest.mark.parametrize(
    ("package_name", "required_context"),
    [
        ("bb", {"outputs"}),
        ("executor", {"inputs", "outputs"}),
    ],
)
def test_binary_darwin_overlay_forwards_flake_context_to_source_apps(
    package_name: str,
    required_context: set[str],
) -> None:
    """Source-app exports must forward every flake argument their package needs."""
    overlay = expect_instance(
        nix_file_expr("overlays/binary-darwin-apps.nix"),
        FunctionDefinition,
    )
    assert {
        argument.name
        for argument in overlay.argument_set
        if isinstance(argument, Identifier)
    } == {
        "final",
        "inputs",
        "outputs",
        "sources",
    }
    assert len(overlay.argument_set) == 5
    assert sum(isinstance(argument, Ellipses) for argument in overlay.argument_set) == 1
    helper = expect_instance(
        expect_scope_binding(overlay.output, "callDarwinAppPackage").value,
        FunctionDefinition,
    )
    helper_call = expect_instance(helper.output, FunctionCall)
    assert_nix_ast_equal(
        helper_call.argument,
        """
        {
          inherit inputs outputs;
          selfSource = sources.${name};
        }
        """,
    )

    package = expect_instance(
        nix_file_expr(f"packages/{package_name}/default.nix"),
        FunctionDefinition,
    )
    package_arguments = {
        argument.name
        for argument in package.argument_set
        if isinstance(argument, Identifier)
    }
    assert required_context <= package_arguments
    exports = expect_instance(overlay.output, AttributeSet)
    assert_nix_ast_equal(
        expect_binding(exports.values, package_name).value,
        f'callDarwinAppPackage "{package_name}"',
    )


@pytest.mark.parametrize(
    ("route_name", "package_name"),
    [
        ("executor", "executor"),
        ("openchamber", "openchamber"),
        ("paseo", "paseo"),
        ("reflect", "reflect-open"),
        ("unsloth", "unsloth"),
    ],
)
def test_migrated_app_is_routed_as_a_system_application(
    route_name: str,
    package_name: str,
) -> None:
    """Migrated apps should replace unmanaged /Applications bundles in place."""
    routing = expect_instance(
        nix_source_fragment_expr(
            "home/george/work.nix",
            "  routing = ",
            ";\n  projection =",
        ),
        AttributeSet,
    )
    route = expect_instance(
        expect_binding(routing.values, route_name).value,
        FunctionCall,
    )
    assert_nix_ast_equal(route.name, "systemApp")
    assert route.argument is not None
    assert_nix_ast_equal(route.argument, f"pkgs.{package_name}")
