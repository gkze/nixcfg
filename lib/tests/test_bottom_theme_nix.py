"""Semantic checks for the evaluator-visible Bottom theme."""

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_source_fragment_expr


def test_bottom_source_is_a_pinned_non_flake_input() -> None:
    """The evaluator-visible source stays pinned to the Catppuccin port revision."""
    bottom_source = nix_source_fragment_expr(
        "flake.nix",
        "    catppuccin-bottom-src = ",
        ";\n    # Temporary fork pin",
    )

    assert_nix_ast_equal(
        bottom_source,
        """
        {
          url = "github:catppuccin/bottom/eadd75acd0ecad4a58ade9a1d6daa3b97ccec07c";
          flake = false;
        }
        """,
    )


def test_bottom_overrides_catppuccin_source_with_guarded_evaluator_visible_input() -> (
    None
):
    """The upstream module keeps ownership while reading evaluator-visible TOML."""
    source_override = nix_source_fragment_expr(
        "home/george/configuration.nix",
        "    sources.bottom =\n      ",
        ";\n    eza.enable",
    )
    assert_nix_ast_equal(
        source_override,
        """
        let
          inputSource = inputs.catppuccin-bottom-src;
          moduleSource =
            (lib.importJSON "${inputs.catppuccin}/pkgs/sources.json").bottom;
        in
        assert lib.assertMsg
          (inputSource.rev == moduleSource.rev
            && inputSource.narHash == moduleSource.hash)
          "catppuccin-bottom-src is out of sync with catppuccin/nix's Bottom source.";
        "${inputSource}/themes"
        """,
    )

    catppuccin_bottom_enable = nix_source_fragment_expr(
        "home/george/configuration.nix",
        "    bottom.enable = ",
        ";\n    # The upstream module imports",
    )
    assert_nix_ast_equal(catppuccin_bottom_enable, "true")
