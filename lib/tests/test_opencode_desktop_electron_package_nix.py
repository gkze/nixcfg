"""AST- and eval-level tests for OpenCode Desktop Electron packaging."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from nix_manipulator.expressions.assertion import Assertion
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.indented_string import IndentedString
from nix_manipulator.expressions.parenthesis import Parenthesis
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT

_SUPPORTED_PLATFORMS = [
    "aarch64-darwin",
    "x86_64-darwin",
    "aarch64-linux",
    "x86_64-linux",
]


@cache
def _package_assertion() -> Assertion:
    """Parse the package and return the outer top-level assertion."""
    root = expect_instance(
        parse_nix_expr(
            Path(
                REPO_ROOT / "packages/opencode-desktop-electron/default.nix"
            ).read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, Assertion)


@cache
def _derivation() -> FunctionCall:
    """Return the final ``stdenv.mkDerivation`` call under the assertions."""
    inner_assertion = expect_instance(_package_assertion().body, Assertion)
    return expect_instance(inner_assertion.body, FunctionCall)


@cache
def _derivation_args() -> AttributeSet:
    """Return the attribute set passed to ``stdenv.mkDerivation``."""
    return expect_instance(_derivation().argument, AttributeSet)


@cache
def _sources_payload() -> dict[str, object]:
    """Load the package's persisted source metadata."""
    return json.loads(
        Path(REPO_ROOT / "packages/opencode-desktop-electron/sources.json").read_text(
            encoding="utf-8"
        )
    )


def test_opencode_desktop_electron_top_level_derivation_keeps_both_guards() -> None:
    """The package should stay wrapped in both version assertions."""
    outer_assertion = _package_assertion()
    inner_assertion = expect_instance(outer_assertion.body, Assertion)

    assert_nix_ast_equal(outer_assertion.expression, "desktopPackageVersionCheck")
    assert_nix_ast_equal(inner_assertion.expression, "electronRuntimeVersionCheck")
    assert_nix_ast_equal(_derivation().name, "stdenv.mkDerivation")


def test_opencode_desktop_electron_uses_exact_nixcfg_electron_runtime() -> None:
    """All supported platforms should source Electron from the exact shared runtime."""
    package = _package_assertion()

    assert_nix_ast_equal(
        expect_scope_binding(package, "electronRuntime").value,
        "nixcfgElectron.runtimeFor electronVersion",
    )
    assert_nix_ast_equal(
        expect_scope_binding(package, "electronRuntimeVersion").value,
        "electronRuntime.version",
    )
    assert_nix_ast_equal(
        expect_scope_binding(package, "electronDist").value,
        "electronRuntime.passthru.dist",
    )


def test_opencode_desktop_electron_node_modules_derivation_tracks_platform_hashes() -> (
    None
):
    """The FOD should stay platform-specific and keep the package-side hash lookup."""
    package = _package_assertion()
    node_modules = expect_instance(
        expect_scope_binding(package, "node_modules").value,
        FunctionCall,
    )
    override = expect_instance(
        expect_instance(node_modules.argument, Parenthesis).value,
        FunctionDefinition,
    )
    override_args = expect_instance(override.output, AttributeSet)

    assert_nix_ast_equal(
        expect_scope_binding(package, "bunTarget").value,
        """
        {
          aarch64-darwin = {
            cpu = "arm64";
            os = "darwin";
          };
          x86_64-darwin = {
            cpu = "x64";
            os = "darwin";
          };
          aarch64-linux = {
            cpu = "arm64";
            os = "linux";
          };
          x86_64-linux = {
            cpu = "x64";
            os = "linux";
          };
        }
        .${system} or (throw "Unsupported system ${system} for ${pname}")
        """,
    )
    assert_nix_ast_equal(node_modules.name, "opencode.node_modules.overrideAttrs")
    assert_nix_ast_equal(
        expect_binding(override_args.values, "outputHash").value,
        'slib.sourceHashForPlatform sourceHashPackageName "nodeModulesHash" system',
    )

    build_phase = expect_instance(
        expect_binding(override_args.values, "buildPhase").value,
        IndentedString,
    )
    install_phase = expect_instance(
        expect_binding(override_args.values, "installPhase").value,
        IndentedString,
    )
    assert "--filter './packages/core'" in build_phase.value
    assert 'cp -R node_modules "$out/node_modules"' in install_phase.value
    assert "packages/core" in install_phase.value
    assert "packages/sdk/js" in install_phase.value
    assert "packages/script" in install_phase.value
    assert 'cp -R --parents "$workspace/node_modules" "$out"' in install_phase.value
    assert "find . -type d -name node_modules" not in install_phase.value


def test_opencode_desktop_electron_linux_desktop_item_is_structured() -> None:
    """Linux desktop metadata should be declared as data, not heredoc text."""
    desktop_item = expect_instance(
        expect_scope_binding(_package_assertion(), "linuxDesktopItem").value,
        FunctionCall,
    )
    desktop_item_args = expect_instance(desktop_item.argument, AttributeSet)

    assert_nix_ast_equal(desktop_item.name, "makeDesktopItem")
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "name").value, "pname"
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "desktopName").value,
        "appName",
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "exec").value,
        '"${pname} %U"',
    )
    assert_nix_ast_equal(
        expect_binding(desktop_item_args.values, "mimeTypes").value,
        '[ "x-scheme-handler/${appProtocolScheme}" ]',
    )


def test_opencode_desktop_electron_derivation_keeps_platform_branches() -> None:
    """Build, install, and install-check phases should stay platform-conditional."""
    derivation_args = _derivation_args()

    for phase_name in ("buildPhase", "installPhase", "installCheckPhase"):
        phase = expect_instance(
            expect_binding(derivation_args.values, phase_name).value,
            IfExpression,
        )
        assert_nix_ast_equal(phase.condition, "stdenv.hostPlatform.isDarwin")
        assert isinstance(phase.consequence, IndentedString)
        assert isinstance(phase.alternative, IndentedString)


def test_opencode_desktop_electron_sources_platforms_match_supported_matrix() -> None:
    """Persisted source hashes should advertise the full supported platform matrix."""
    hashes = _sources_payload().get("hashes")

    assert isinstance(hashes, list)
    assert sorted(entry["platform"] for entry in hashes) == sorted(_SUPPORTED_PLATFORMS)


def test_opencode_desktop_electron_env_exports_runtime_identity_overrides() -> None:
    """The derivation should export the runtime identity overrides used by the Electron patch."""
    env = expect_instance(
        expect_binding(_derivation_args().values, "env").value, AttributeSet
    )

    assert_nix_ast_equal(expect_binding(env.values, "OPENCODE_APP_ID").value, "appId")
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_APP_NAME").value, "appName"
    )
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_PROTOCOL_NAME").value, "appProtocolName"
    )
    assert_nix_ast_equal(
        expect_binding(env.values, "OPENCODE_PROTOCOL_SCHEME").value,
        "appProtocolScheme",
    )


def test_opencode_desktop_electron_exposes_copy_mode_mac_app_metadata() -> None:
    """The package should expose shared mac-app metadata for /Applications routing."""
    passthru = expect_instance(
        expect_binding(_derivation_args().values, "passthru").value,
        AttributeSet,
    )
    mac_app = expect_instance(
        expect_binding(passthru.values, "macApp").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleName").value, "appBundleName"
    )
    assert_nix_ast_equal(
        expect_binding(mac_app.values, "bundleRelPath").value,
        '"Applications/${appBundleName}"',
    )
    assert_nix_ast_equal(expect_binding(mac_app.values, "installMode").value, '"copy"')
