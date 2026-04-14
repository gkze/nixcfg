# Override flakelight's auto-import to centralize package discovery and
# per-system filtering in `packages/registry.nix`. Without this,
# `nix flake check --all-systems` fails because nixpkgs' check-meta asserts on
# meta.platforms mismatches.
#
# When `packages/default.nix` exists, flakelight imports it directly instead of
# auto-importing the directory contents, so this file is where flake package
# outputs are materialized from the shared registry.
{
  system ? null,
  lib,
  outputs,
  ...
}:
let
  packageMaterialization = import ../lib/package-materialization.nix {
    src = ../.;
    inherit lib outputs;
  };

  systemEval = builtins.tryEval system;
  resolvedSystem =
    if systemEval.success && systemEval.value != null then systemEval.value else "x86_64-linux";
in
packageMaterialization.packageFunctionsForSystem resolvedSystem
