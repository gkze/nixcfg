# Shared package materialization helper used by both `default.nix` and
# `packages/default.nix` so flake consumers and flakelight see the same
# per-system package set and `selfSource` injection behavior.
{
  src,
  lib ? null,
  outputs ? null,
}:
let
  packageRegistry = import (src + "/packages/registry.nix") { inherit src; };
  inherit (packageRegistry) packagePaths;
  packageNames = builtins.attrNames packagePaths;
  packagePathsForSystem = packageRegistry.forSystem;
  packageSelfSource =
    if lib != null && outputs != null then
      import (src + "/lib/package-self-source.nix") {
        inherit lib outputs;
      }
    else
      null;

  packageFunctionsForSystem =
    system:
    if packageSelfSource == null then
      throw "lib/package-materialization.nix: pass `lib` and `outputs` to materialize package functions."
    else
      builtins.mapAttrs (name: path: packageSelfSource.injectIntoFunction name (import path)) (
        packagePathsForSystem system
      );
in
{
  inherit
    packageFunctionsForSystem
    packageNames
    packagePaths
    packagePathsForSystem
    ;

  callPackagesForSystem =
    {
      pkgs,
      system ? pkgs.stdenv.hostPlatform.system,
      inputs ? { },
      extraPackageArgs ? { },
    }:
    builtins.mapAttrs (
      _name: pkg:
      pkgs.callPackage pkg (
        {
          inherit inputs outputs;
        }
        // extraPackageArgs
      )
    ) (packageFunctionsForSystem system);
}
