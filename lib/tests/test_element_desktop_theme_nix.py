"""Semantic checks for the evaluator-visible Element Desktop theme."""

from nix_manipulator.expressions.function.definition import FunctionDefinition
from nix_manipulator.expressions.set import AttributeSet

from lib.tests._assertions import expect_instance
from lib.tests._nix_ast import assert_nix_ast_equal, expect_binding
from lib.tests._nix_source import nix_file_expr, nix_source_fragment_expr


def test_element_desktop_source_is_a_pinned_non_flake_input() -> None:
    """The evaluator-visible source stays pinned to the Catppuccin port revision."""
    element_source = nix_source_fragment_expr(
        "flake.nix",
        "    catppuccin-element-src = ",
        ";\n    catppuccin-bat",
    )

    assert_nix_ast_equal(
        element_source,
        """
        {
          url = "github:catppuccin/element/f8236600302ef016c7366b96414a09e086996b71";
          flake = false;
        }
        """,
    )


def test_element_desktop_uses_guarded_evaluator_visible_source() -> None:
    """The upstream module reads its theme from an evaluator-visible source."""
    source_override = nix_source_fragment_expr(
        "home/george/configuration.nix",
        "    sources.element =\n      ",
        ";\n    vscode.profiles.default",
    )

    assert_nix_ast_equal(
        source_override,
        """
        let
          inputSource = inputs.catppuccin-element-src;
          moduleSource =
            (lib.importJSON "${inputs.catppuccin}/pkgs/sources.json").element;
        in
        assert lib.assertMsg
          (inputSource.rev == moduleSource.rev
            && inputSource.narHash == moduleSource.hash)
          "catppuccin-element-src is out of sync with catppuccin/nix's Element source.";
        "${inputSource}/themes"
        """,
    )

    catppuccin_element_enable = nix_source_fragment_expr(
        "home/george/configuration.nix",
        "    element-desktop.enable = ",
        ";\n",
    )
    assert_nix_ast_equal(catppuccin_element_enable, "true")

    module = expect_instance(
        nix_file_expr("home/george/configuration.nix"),
        FunctionDefinition,
    )
    config = expect_instance(module.output, AttributeSet)
    programs = expect_instance(
        expect_binding(config.values, "programs").value,
        AttributeSet,
    )
    element_desktop = expect_instance(
        expect_binding(programs.values, "element-desktop").value,
        AttributeSet,
    )

    assert_nix_ast_equal(
        element_desktop,
        """
        {
          enable = true;
          package = null;
        }
        """,
    )
