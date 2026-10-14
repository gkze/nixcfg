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

  # Mirrors how nixpkgs derives hostPlatform.isDarwin, so the platform key stays
  # the thing under test rather than the guard's spelling.
  fragmentFor =
    system:
    import (src + "/overlays/binary-darwin-apps.nix") {
      prev = {
        lib.optionalAttrs = cond: attrs: if cond then attrs else { };
        stdenv.hostPlatform = {
          inherit system;
          isDarwin = builtins.match ".*-darwin" system != null;
        };
      };
      final = {
        callPackage = path: _args: builtins.baseNameOf (toString path);
      };
      inputs = { };
      outputs = { };
      sources = { };
    };

  darwin = fragmentFor "aarch64-darwin";
  checks = [
    (
      assertEq "x86_64-linux receives no Darwin-only app attributes" { } (
        fragmentFor "x86_64-linux"
      )
    )
    (
      assertEq "aarch64-linux receives no Darwin-only app attributes" { } (
        fragmentFor "aarch64-linux"
      )
    )
    (
      assertEq "aarch64-darwin keeps the app attributes" true (
        builtins.isAttrs darwin && darwin ? airfoil && darwin ? baseten-switch
      )
    )
    (assertEq "a Darwin export resolves its own package directory" "airfoil" darwin.airfoil)
  ];
in
builtins.deepSeq checks true
