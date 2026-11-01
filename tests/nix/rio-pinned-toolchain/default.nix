# Evaluates the real aarch64-darwin package set: Rio's build inputs must carry
# the exact rust-bin toolchain its sources.json pins, not nixpkgs' rustc.
{
  pkgs,
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq;

  pin =
    (builtins.fromJSON (builtins.readFile (src + "/packages/rio/sources.json"))).pins.rustToolchain;
  toolchain = pkgs.rust-bin.stable.${pin}.default;
  inputPaths = map (input: input.drvPath) pkgs.rio.nativeBuildInputs;

  checks = [
    (assertEq "Rio builds with rust-bin stable ${pin}" true (
      builtins.elem toolchain.drvPath inputPaths
    ))
    (assertEq "Rio does not build with nixpkgs' rustc" false (
      builtins.elem pkgs.rustc.drvPath inputPaths || builtins.elem pkgs.cargo.drvPath inputPaths
    ))
  ];
in
builtins.deepSeq checks true
