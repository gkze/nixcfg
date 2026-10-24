# Exposes registry packages through `overlays.default`. packages/registry.nix
# owns both the package list (`overlayExposure`) and each package's systems;
# this fragment only calls the packages. In the flake, flakelight's package
# overlay replaces these attributes with the same calls, so the values that
# matter here are the ones the default.nix compatibility API and the update
# probes in lib/update/nix.py see.
{
  final,
  inputs,
  outputs,
  prev,
  system,
  ...
}:
let
  registry = import ../packages/registry.nix { src = ../.; };
  packageSelfSource = import ../lib/package-self-source.nix {
    inherit (prev) lib;
    inherit outputs;
  };
  callRegistryPackage =
    name:
    final.callPackage (packageSelfSource.injectIntoFunction name (
      import registry.packagePaths.${name}
    )) { inherit inputs outputs; };
in
prev.lib.genAttrs (registry.overlayPackageNamesFor system) callRegistryPackage
