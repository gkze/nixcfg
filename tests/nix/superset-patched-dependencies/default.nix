{
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq;

  patchesFor =
    args:
    import (src + "/packages/superset/patched-dependencies.nix") (
      {
        src = "/upstream";
      }
      // args
    );

  manifest = {
    patchedDependencies = {
      "@xterm/addon-ligatures@0.11.0-beta.310" = "patches/@xterm%2Faddon-ligatures@0.11.0-beta.310.patch";
      "metro@0.84.5" = "patches/metro@0.84.5.patch";
      "metro@0.87.1" = "patches/metro@0.87.1.patch";
    };
  };

  checks = [
    (assertEq "installed exact versions receive upstream patches"
      {
        "@xterm/addon-ligatures@0.11.0-beta.310" =
          "/upstream/patches/@xterm%2Faddon-ligatures@0.11.0-beta.310.patch";
        "metro@0.87.1" = "/upstream/patches/metro@0.87.1.patch";
      }
      (patchesFor {
        inherit manifest;
        packageNames = [
          "@xterm/addon-ligatures@0.11.0-beta.310"
          "metro@0.87.1"
          "zod@4.6.5"
        ];
      })
    )
    (assertEq "a manifest without patchedDependencies patches nothing" { } (patchesFor {
      manifest = { };
      packageNames = [ "metro@0.87.1" ];
    }))
    (assertEq "declared versions bun does not install are not patched" { } (patchesFor {
      inherit manifest;
      packageNames = [ "metro@0.84.6" ];
    }))
  ];
in
builtins.deepSeq checks true
