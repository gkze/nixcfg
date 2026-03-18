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

  companionPackages = discovery.discoverCompanionEntries {
    root = pkgDir;
    directories = discoveredPackages.dirNames;
    fileName = "crate2nix-src.nix";
  };

  discoveredPackagePaths = builtins.listToAttrs (
    builtins.map (name: {
      inherit name;
      value = discoveredPackages.pathFor name;
    }) (builtins.filter (name: !(builtins.elem name disabledPackages)) discoveredPackages.names)
  );

  companionPackagePaths = builtins.listToAttrs (
    builtins.map (name: {
      inherit name;
      value = companionPackages.pathFor name;
    }) companionPackages.names
  );

  packagePaths = discoveredPackagePaths // companionPackagePaths;

  darwinOnly = [
    "commander"
    "codex-desktop"
    "conductor"
    "opencode-desktop-crate2nix-src"
    "zed-editor-nightly-crate2nix-src"
  ];

  sculptorSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];

  supersetSystems = [
    "aarch64-darwin"
    "x86_64-linux"
  ];

  emdashSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];

  packageSystemConstraints = {
    commander = system: builtins.match ".*-darwin" system != null;
    codex-desktop = system: builtins.match ".*-darwin" system != null;
    conductor = system: builtins.match ".*-darwin" system != null;
    opencode-desktop-crate2nix-src = system: builtins.match ".*-darwin" system != null;
    emdash = system: builtins.elem system emdashSystems;
    sculptor = system: builtins.elem system sculptorSystems;
    superset = system: builtins.elem system supersetSystems;
    zed-editor-nightly-crate2nix-src = system: builtins.match ".*-darwin" system != null;
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
