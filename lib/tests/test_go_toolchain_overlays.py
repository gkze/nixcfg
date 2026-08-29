"""Go toolchain contracts for overlays that track fast-moving upstreams."""

from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr
from lib.update.paths import REPO_ROOT


def test_gogcli_uses_go_1_27_without_weakening_upstreams_floor() -> None:
    """Build current gogcli with a compiler that satisfies its declared minimum."""
    overlay = expect_instance(
        nix_file_expr(REPO_ROOT / "overlays/gogcli/default.nix"),
        FunctionDefinition,
    )
    output = expect_instance(overlay.output, AttributeSet)
    call = expect_instance(expect_binding(output.values, "gogcli").value, FunctionCall)
    arguments = expect_instance(call.argument, AttributeSet)

    assert_nix_ast_equal(expect_binding(arguments.values, "go").value, "final.go_1_27")
    assert "postPatch" not in arguments.values


def test_crush_overrides_nixpkgs_versioned_go_builder() -> None:
    """Use go_latest when nixpkgs exposes Crush through buildGo126Module."""
    overlay = expect_instance(
        nix_file_expr(REPO_ROOT / "overlays/crush/default.nix"),
        FunctionDefinition,
    )
    output = expect_instance(overlay.output, AttributeSet)

    assert_nix_ast_equal(
        expect_binding(output.scope, "crushBase").value,
        """
        if crushOverrideArgs ? buildGoModule then
          prev.crush.override {
            buildGoModule = prev.buildGoModule.override { inherit go; };
          }
        else if crushOverrideArgs ? buildGo126Module then
          prev.crush.override {
            buildGo126Module = prev.buildGo126Module.override { inherit go; };
          }
        else
          prev.crush
        """,
    )
