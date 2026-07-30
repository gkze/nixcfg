{
  src ? ../../..,
}:
let
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  fragmentFor =
    system:
    import (src + "/overlays/rio") {
      prev = {
        lib.optionalAttrs = cond: attrs: if cond then attrs else { };
        stdenv.hostPlatform = { inherit system; };
        rio.overrideAttrs = _: "overridden";
      };
      selfSource = { };
      slib = { };
    };

  checks = [
    (assertEq "x86_64-linux leaves nixpkgs Rio unchanged" { } (fragmentFor "x86_64-linux"))
    (assertEq "aarch64-linux leaves nixpkgs Rio unchanged" { } (fragmentFor "aarch64-linux"))
    (assertEq "x86_64-darwin leaves nixpkgs Rio unchanged" { } (fragmentFor "x86_64-darwin"))
    (assertEq "aarch64-darwin overrides Rio" "overridden" (fragmentFor "aarch64-darwin").rio)
  ];
in
builtins.deepSeq checks true
