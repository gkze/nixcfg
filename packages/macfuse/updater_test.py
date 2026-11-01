"""Tests for the macFUSE package build."""

from lib.update.paths import REPO_ROOT


def test_macfuse_unpacks_the_apfs_dmg_with_7zz() -> None:
    """The 5.5.0 macFUSE DMG is APFS, which undmg (HFS+ only) cannot read."""
    default_nix = (REPO_ROOT / "packages/macfuse/default.nix").read_text(
        encoding="utf-8"
    )

    assert '7zz x -sns- -snld -y "$src"' in default_nix
    assert "_7zz" in default_nix
    assert 'undmg "$src"' not in default_nix
    assert "  undmg\n" not in default_nix
