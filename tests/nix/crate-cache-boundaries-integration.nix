# Opt-in integration probe; intentionally excluded from the default check graph
# because it evaluates two complete crate2nix package graphs per package.
# Run from the repository root with:
#
#   nix eval --offline --impure --option allow-import-from-derivation false \
#     --json --file tests/nix/crate-cache-boundaries-integration.nix
let
  root = ../..;
  outputs = builtins.getFlake "git+file://${toString root}";
  system = builtins.currentSystem;
  supportedSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];
  pkgs = outputs.pkgs.${system};
  args = {
    inherit (outputs) inputs;
    inherit outputs pkgs;
  };
in
assert builtins.elem system supportedSystems;
{
  gitbutler = (import ../../packages/gitbutler/tests/release-metadata-cache-boundary.nix args).check;
  zed =
    (import ../../packages/zed-editor-nightly/tests/release-metadata-cache-boundary.nix args).check;
}
