# Derivation identities for one system.
#
# The pull-request gate compares these drv paths at the merge base and the
# PR head. Evaluation must not build the outputs: import-from-derivation
# stays disabled by the caller. `root-closure-<name>` is one host or home
# root, the unit Update realizes as a Darwin shard. The aggregate
# `root-closures` linkFarm is not a separate target; its drv changes
# whenever a member does, and building it would realize every root.
{ flakePath, system }:
let
  flake = builtins.getFlake "path:${flakePath}?lastModified=1";
  lib = flake.inputs.nixpkgs.lib;
  packages =
    if flake ? packages && flake.packages ? ${system} then
      flake.packages.${system}
    else
      throw "closure gate: flake.packages.${system} is missing";
  checks =
    if flake ? checks && flake.checks ? ${system} then flake.checks.${system} else { };
  drvOf = name: value: value.drvPath;
  packageNames = builtins.attrNames packages;
  notDerivations = builtins.filter (name: !lib.isDerivation packages.${name}) packageNames;
  rootNames = builtins.filter (lib.hasPrefix "root-closure-") (builtins.attrNames checks);
  rootsNotDerivations = builtins.filter (name: !lib.isDerivation checks.${name}) rootNames;
in
assert lib.assertMsg (notDerivations == [ ]) (
  "closure gate: package outputs are not derivations: " + builtins.concatStringsSep ", " notDerivations
);
assert lib.assertMsg (rootsNotDerivations == [ ]) (
  "closure gate: root closures are not derivations: "
  + builtins.concatStringsSep ", " rootsNotDerivations
);
{
  packages = builtins.listToAttrs (
    map (name: {
      inherit name;
      value = drvOf name packages.${name};
    }) packageNames
  );
  roots = builtins.listToAttrs (
    map (name: {
      inherit name;
      value = drvOf name checks.${name};
    }) rootNames
  );
}
