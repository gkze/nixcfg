{
  src ? ../.,
}:
let
  pkgDir = src + "/packages";
  discovery = import ../lib/discovery.nix;

  reservedFileNames = [
    "default.nix"
    "registry.nix"
  ];

  disabledPackages = [ ];

  discoveredPackages = discovery.discoverDefaultNixEntries {
    root = pkgDir;
    excludeFiles = reservedFileNames;
    includeFile = fileName: _: builtins.match "^_.*\\.nix$" fileName == null;
  };

  packagePaths = builtins.listToAttrs (
    builtins.map (name: {
      inherit name;
      value = discoveredPackages.pathFor name;
    }) (builtins.filter (name: !(builtins.elem name disabledPackages)) discoveredPackages.names)
  );

  darwinOnly = [
    "codex-desktop"
    "conductor"
  ];

  sculptorSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];

  supersetSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];

  packageSystemConstraints = {
    codex-desktop = system: builtins.match ".*-darwin" system != null;
    conductor = system: builtins.match ".*-darwin" system != null;
    sculptor = system: builtins.elem system sculptorSystems;
    superset = system: builtins.elem system supersetSystems;
  };

  unsupportedForSystem =
    system:
    builtins.filter (name: !(packageSystemConstraints.${name} system)) (
      builtins.attrNames packageSystemConstraints
    );
in
{
  inherit
    darwinOnly
    packagePaths
    sculptorSystems
    ;

  forSystem = system: builtins.removeAttrs packagePaths (unsupportedForSystem system);
}
