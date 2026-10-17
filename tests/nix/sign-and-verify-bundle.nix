# Rendering contract of `signAndVerifyBundle`
# (overlays/_lib/helpers/darwin-apps.nix). The helper only builds shell text,
# so exact strings are the contract; this evaluates the helper alone with no
# package graph.
{ pkgs }:
let
  inherit (pkgs) lib;
  helpers = import ../../overlays/_lib/helpers/darwin-apps.nix {
    prev = pkgs;
    inherit (pkgs.stdenv.hostPlatform) system;
  };
  sign = helpers.signAndVerifyBundle;
  entitlements = builtins.toFile "Entitlements.plist" "<plist/>";
  cases = [
    {
      name = "minimal sign keeps the historical codesignApp text";
      actual = sign {
        bundle = ''"$out/Applications/A.app"'';
        verify = false;
      };
      expected = ''
        /usr/bin/codesign --force --deep --sign - "$out/Applications/A.app"
      '';
    }
    {
      name = "preserved metadata, entitlements and verification";
      actual = sign {
        bundle = ''"$app_bundle"'';
        preserveMetadata = [
          "identifier"
          "flags"
          "runtime"
        ];
        inherit entitlements;
      };
      expected = ''
        /usr/bin/codesign --force --deep --sign - --preserve-metadata=identifier,flags,runtime --entitlements ${entitlements} "$app_bundle"
        /usr/bin/codesign --verify --deep --strict "$app_bundle"
      '';
    }
  ];
  failures = builtins.filter (case: case.actual != case.expected) cases;
in
lib.assertMsg (failures == [ ]) (
  "signAndVerifyBundle rendering changed:\n"
  + lib.concatMapStringsSep "\n" (
    case: "${case.name}:\n--- expected\n${case.expected}--- actual\n${case.actual}"
  ) failures
)
