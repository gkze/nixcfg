"""Semantic checks for the evaluator-visible Starship palette source."""

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_source_fragment_expr


def test_starship_source_is_a_pinned_non_flake_input() -> None:
    """The palette source stays pinned to catppuccin/nix's Starship revision."""
    starship_source = nix_source_fragment_expr(
        "flake.nix",
        "    catppuccin-starship-src = ",
        ";\n    catppuccin-element-src = ",
    )

    assert_nix_ast_equal(
        starship_source,
        """
        {
          url = "github:catppuccin/starship/5906cc369dd8207e063c0e6e2d27bd0c0b567cb8";
          flake = false;
        }
        """,
    )


def test_appearance_reads_starship_palettes_without_import_from_derivation() -> None:
    """Darwin roots must evaluate on Linux validators, which cannot build Darwin."""
    starship_themes = nix_source_fragment_expr(
        "home/george/appearance.nix",
        "  starshipThemes =\n    ",
        ";\n  templates = ",
    )
    assert_nix_ast_equal(
        starship_themes,
        """
        let
          inputSource = inputs.catppuccin-starship-src;
          moduleSource =
            (lib.importJSON "${inputs.catppuccin}/pkgs/sources.json").starship;
        in
        assert lib.assertMsg
          (inputSource.rev == moduleSource.rev
            && inputSource.narHash == moduleSource.hash)
          "catppuccin-starship-src is out of sync with catppuccin/nix's Starship source.";
        "${inputSource}/themes"
        """,
    )
