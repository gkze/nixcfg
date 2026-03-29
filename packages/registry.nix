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

  helperEntries = [
    "go-cli-wrapper"
    "openchamber-bun"
    "registry"
  ];

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
    }) discoveredPackages.names
  );

  companionPackagePaths = builtins.listToAttrs (
    builtins.map (name: {
      inherit name;
      value = companionPackages.pathFor name;
    }) companionPackages.names
  );

  packagePaths = discoveredPackagePaths // companionPackagePaths;

  supportedSystemsByGroup = {
    emdash = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    gooseAarch64Darwin = [ "aarch64-darwin" ];
    sculptor = [
      "aarch64-darwin"
      "x86_64-linux"
    ];
    superset = [
      "aarch64-darwin"
      "x86_64-linux"
    ];
  };

  packageConstraintGroups = {
    commander = "darwin";
    codex-desktop = "darwin";
    conductor = "darwin";
    emdash = "emdash";
    goose-cli = "gooseAarch64Darwin";
    opencode-desktop-crate2nix-src = "darwin";
    sculptor = "sculptor";
    superset = "superset";
    zed-editor-nightly = "darwin";
    zed-editor-nightly-crate2nix-src = "darwin";
  };

  groupConstraintPredicates = {
    darwin = system: builtins.match ".*-darwin" system != null;
    emdash = system: builtins.elem system supportedSystemsByGroup.emdash;
    gooseAarch64Darwin = system: builtins.elem system supportedSystemsByGroup.gooseAarch64Darwin;
    sculptor = system: builtins.elem system supportedSystemsByGroup.sculptor;
    superset = system: builtins.elem system supportedSystemsByGroup.superset;
  };

  packageSystemConstraints = builtins.mapAttrs (
    _name: group: groupConstraintPredicates.${group}
  ) packageConstraintGroups;

  darwinOnly = builtins.filter (name: packageConstraintGroups.${name} == "darwin") (
    builtins.attrNames packageConstraintGroups
  );

  sculptorSystems = supportedSystemsByGroup.sculptor;

  unsupportedForSystem =
    system:
    builtins.filter (name: !(packageSystemConstraints.${name} system)) (
      builtins.attrNames packageSystemConstraints
    );
in
{
  inherit
    darwinOnly
    helperEntries
    packagePaths
    sculptorSystems
    ;

  forSystem = system: builtins.removeAttrs packagePaths (unsupportedForSystem system);
}
