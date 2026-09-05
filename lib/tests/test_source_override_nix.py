"""Structural contracts for source-backed package version overrides."""

from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._nix_source import (
    nix_file_binding_expr,
    nix_file_inherited_attr_source_expr,
)


def test_google_chrome_consumes_its_platform_version_pin_locally() -> None:
    """Chrome should need only the shared source override and mac-app wrapper."""
    chrome = nix_file_binding_expr("overlays/default.nix", "google-chrome")

    assert_nix_ast_equal(
        chrome,
        'withManagedMacApp (final.mkSourceOverride "google-chrome" prev.google-chrome) "Google Chrome.app"',
    )


def test_source_override_selects_platform_version_with_platform_artifact() -> None:
    """Version, URL, and hash selection happen in one source override layer."""
    source_override = nix_file_binding_expr(
        "overlays/_lib/helpers/sources.nix",
        "mkSourceOverride",
    )

    assert_nix_ast_equal(
        source_override,
        """
        name: pkg:
        let
          info = sources.${name};
        in
        pkg.overrideAttrs {
          version = (info.pins or { }).${system} or info.version;
          src = prev.fetchurl {
            url = info.urls.${system} or (throw "sources.${name}.urls missing ${system}");
            hash = info.hashes.${system};
          };
        }
        """,
    )


def test_superset_consumes_its_updater_owned_version() -> None:
    """Superset's source build must not reinterpret its flake release tag."""
    version_source = nix_file_inherited_attr_source_expr(
        "packages/superset/default.nix",
        "version",
    )

    assert_nix_ast_equal(version_source, "selfSource")
