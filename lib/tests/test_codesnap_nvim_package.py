"""Structural checks for the CodeSnap Neovim overlay."""

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import nix_source_fragment_expr


def test_codesnap_override_preserves_upstream_loading_contract() -> None:
    """Apply behavior deltas without replacing nixpkgs' loader postPatch."""
    override = nix_source_fragment_expr(
        "overlays/vim-plugin-overrides.nix",
        "      codesnap-nvim = ",
        ";\n\n      nvim-treesitter-textobjects",
    )

    assert_nix_ast_equal(
        override,
        """
        vprev.codesnap-nvim.overrideAttrs (old: {
          patches = (old.patches or [ ]) ++ [ ./codesnap-nvim.patch ];
        })
        """,
    )
