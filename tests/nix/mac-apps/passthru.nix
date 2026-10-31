# The single definition of the `passthru.macApp` contract that lib/mac-apps.nix
# consumes: an Applications/ bundle copied into place unless the package
# overrides it. Asserted on the returned value, not on the helper's source.
{
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq;

  mkMacAppPassthru = import (src + "/overlays/_lib/mk-mac-app-passthru.nix");

  defaults = mkMacAppPassthru { bundleName = "Example.app"; };

  overridden = mkMacAppPassthru {
    bundleName = "Example.app";
    macApp = {
      installMode = "symlink";
      extra = 1;
    };
  };

  checks = [
    (assertEq "a managed bundle is copied to Applications/ by default" {
      macApp = {
        bundleName = "Example.app";
        bundleRelPath = "Applications/Example.app";
        installMode = "copy";
      };
    } defaults)
    (assertEq "an explicit macApp wins over every default" {
      macApp = {
        bundleName = "Example.app";
        bundleRelPath = "Applications/Example.app";
        installMode = "symlink";
        extra = 1;
      };
    } overridden)
  ];
in
builtins.deepSeq checks true
