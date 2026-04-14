{
  src ? ../.,
}:
let
  pkgDir = src + "/packages";
  discovery = import ../lib/discovery.nix;

  discoveredPackages = discovery.discoverDefaultNixEntries {
    root = pkgDir;
    excludeFiles = [
      "default.nix"
      "registry.nix"
    ];
    includeFile = fileName: _: builtins.match "^_.*\\.nix$" fileName == null;
  };

  companionPackages = discovery.discoverCompanionEntries {
    root = pkgDir;
    directories = discoveredPackages.dirNames;
    fileName = "crate2nix-src.nix";
  };

  # Keep one internal metadata table so path discovery, helper exposure, and
  # platform guards cannot drift apart.
  basePackageMetadata =
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = {
          path = discoveredPackages.pathFor name;
        };
      }) discoveredPackages.names
    )
    // builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = {
          path = companionPackages.pathFor name;
        };
      }) companionPackages.names
    );

  packageMetadataOverrides = {
    "go-cli-wrapper" = {
      helper = true;
    };
    "openchamber-bun" = {
      helper = true;
    };
    registry = {
      helper = true;
    };
    commander = {
      constraint = "darwin";
    };
    "codex-desktop" = {
      constraint = "darwin";
    };
    conductor = {
      constraint = "darwin";
    };
    granola = {
      constraint = "darwin";
    };
    netnewswire = {
      constraint = "darwin";
    };
    "wispr-flow" = {
      constraint = "darwin";
    };
    emdash = {
      constraint = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
    };
    "goose-cli" = {
      constraint = [ "aarch64-darwin" ];
    };
    "opencode-desktop-crate2nix-src" = {
      constraint = "darwin";
    };
    raycast = {
      constraint = "darwin";
    };
    sculptor = {
      constraint = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    };
    superset = {
      constraint = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
    };
    "zed-editor-nightly" = {
      constraint = "darwin";
    };
    "zed-editor-nightly-crate2nix-src" = {
      constraint = "darwin";
    };
  };

  packageMetadata =
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value =
          {
            helper = false;
            constraint = null;
          }
          // (if builtins.hasAttr name basePackageMetadata then basePackageMetadata.${name} else { })
          // (if builtins.hasAttr name packageMetadataOverrides then packageMetadataOverrides.${name} else { });
      }) (builtins.attrNames (basePackageMetadata // packageMetadataOverrides))
    );

  supportsSystem =
    constraint: system:
    if constraint == null then
      true
    else if builtins.isList constraint then
      builtins.elem system constraint
    else if constraint == "darwin" then
      builtins.match ".*-darwin" system != null
    else
      throw "packages/registry.nix: unsupported system constraint `${constraint}`";

  packageNamesMatching =
    predicate:
    builtins.filter (name: predicate packageMetadata.${name}) (
      builtins.attrNames packageMetadata
    );

  packagePathsMatching =
    predicate:
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = packageMetadata.${name}.path;
      }) (
        packageNamesMatching (
          meta:
          meta ? path
          && !meta.helper
          && predicate meta
        )
      )
    );

  packagePaths = packagePathsMatching (_meta: true);
  helperEntries = packageNamesMatching (meta: meta.helper);
  darwinOnly = packageNamesMatching (meta: meta.constraint == "darwin");
  sculptorSystems = packageMetadata.sculptor.constraint;
in
{
  inherit
    darwinOnly
    helperEntries
    packagePaths
    sculptorSystems
    ;

  forSystem = system: packagePathsMatching (meta: supportsSystem meta.constraint system);
}
