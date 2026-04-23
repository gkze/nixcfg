"""AST-level guardrails for Zed nightly packaging and wiring."""

from __future__ import annotations

from functools import cache

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.if_expression import IfExpression
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import (
    assert_nix_ast_equal,
    expect_binding,
    expect_scope_binding,
    parse_nix_expr,
)
from lib.update.paths import REPO_ROOT


@cache
def _zed_platform_switch() -> IfExpression:
    """Return the top-level platform switch from the Zed package."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/zed-editor-nightly/default.nix").read_text(
                encoding="utf-8"
            )
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, IfExpression)


@cache
def _home_configuration_output() -> AttributeSet:
    """Return George's standalone Home Manager configuration attrset."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "home/george/configuration.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


@cache
def _zed_module_output() -> AttributeSet:
    """Return George's shared Zed module attrset."""
    root = expect_instance(
        parse_nix_expr((REPO_ROOT / "home/george/zed.nix").read_text(encoding="utf-8")),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


@cache
def _registry_output() -> AttributeSet:
    """Return the package registry export attrset."""
    root = expect_instance(
        parse_nix_expr(
            (REPO_ROOT / "packages/registry.nix").read_text(encoding="utf-8")
        ),
        FunctionDefinition,
    )
    return expect_instance(root.output, AttributeSet)


def test_zed_fontconfig_uses_raw_source_paths() -> None:
    """Avoid forcing the patched source derivation during cross-platform eval."""
    common_override = expect_instance(
        expect_scope_binding(_zed_platform_switch(), "commonOverride").value,
        FunctionDefinition,
    )
    common_override_output = expect_instance(common_override.output, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(common_override_output.values, "FONTCONFIG_FILE").value,
        """
        makeFontsConf {
          fontDirectories = [
            "${src}/assets/fonts/lilex"
            "${src}/assets/fonts/ibm-plex-sans"
          ];
        }
        """,
    )


def test_zed_package_keeps_linux_and_darwin_branches_while_exporting_validated_surfaces() -> (
    None
):
    """Keep both platform branches present while exporting only validated outputs."""
    alternative = expect_instance(_zed_platform_switch().alternative, FunctionCall)
    alternative_args = expect_instance(alternative.argument, AttributeSet)
    meta = expect_instance(
        expect_binding(alternative_args.values, "meta").value, AttributeSet
    )

    assert_nix_ast_equal(
        expect_scope_binding(_zed_platform_switch(), "commonCrates").value,
        """
        if pkgs.stdenv.hostPlatform.isLinux then
          builtins.attrNames cargoNix.internal.crates
        else
          builtins.attrNames cargoNix.workspaceMembers
        """,
    )
    assert_nix_ast_equal(
        expect_binding(meta.values, "platforms").value,
        '[ "aarch64-darwin" "x86_64-linux" ]',
    )


def test_zed_disables_unneeded_perf_binary_in_dependency_builds() -> None:
    """Avoid Linux multi-output cycles from the perf tooling crate's binary target."""
    assert_nix_ast_equal(
        expect_scope_binding(_zed_platform_switch(), "perfOverride").value,
        """
        _attrs: {
          crateBin = [ ];
        }
        """,
    )


def test_standalone_home_config_imports_the_shared_zed_module() -> None:
    """Keep Zed configuration in the shared user module rather than host entrypoints."""
    assert_nix_ast_equal(
        expect_binding(_home_configuration_output().values, "imports").value,
        """
        [
          {
            darwin = ./darwin.nix;
            linux = ./nixos.nix;
          }
          .${slib.kernel system}
          outputs.homeModules.nixcfgLanguageBun
          outputs.homeModules.nixcfgGit
          outputs.homeModules.nixcfgLanguageGo
          ./nixvim.nix
          ./zed.nix
          outputs.homeModules.nixcfgOpencode
          outputs.homeModules.nixcfgPackages
          outputs.homeModules.nixcfgZen
          outputs.homeModules.nixcfgLanguagePython
          outputs.homeModules.nixcfgLanguageRust
          outputs.homeModules.nixcfgStylix
          outputs.homeModules.nixcfgZsh
          inputs.catppuccin.homeModules.catppuccin
        ]
        """,
    )


def test_shared_zed_module_keeps_nightly_package_wiring() -> None:
    """George's shared module should still install and configure the nightly editor."""
    programs = expect_instance(
        expect_binding(_zed_module_output().values, "programs").value,
        AttributeSet,
    )
    zed_editor = expect_instance(
        expect_binding(programs.values, "zed-editor").value,
        AttributeSet,
    )

    assert_nix_ast_equal(expect_binding(zed_editor.values, "enable").value, "true")
    assert_nix_ast_equal(
        expect_binding(zed_editor.values, "package").value,
        "pkgs.zed-editor-nightly",
    )


def test_registry_limits_zed_to_validated_primary_surfaces() -> None:
    """Keep Zed exports constrained to the currently validated Darwin/Linux outputs."""
    overrides = expect_instance(
        expect_scope_binding(_registry_output(), "packageMetadataOverrides").value,
        AttributeSet,
    )

    for package_name in (
        '"zed-editor-nightly"',
        '"zed-editor-nightly-crate2nix-src"',
    ):
        entry = expect_instance(
            expect_binding(overrides.values, package_name).value,
            AttributeSet,
        )
        assert_nix_ast_equal(
            expect_binding(entry.values, "constraint").value,
            '[ "aarch64-darwin" "x86_64-linux" ]',
        )
