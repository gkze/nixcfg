{
  src ? ./.,
  inputs ? { },
  lib ? null,
  pkgsFor ? null,
}:
let
  repoSrc = src;
  nixcfgDir = src + "/lib";
  nixcfgPath = file: nixcfgDir + "/${file}";

  exports = import (nixcfgPath "exports.nix") { inherit src; };
  packageRegistry = import (src + "/packages/registry.nix") { inherit src; };

  initialLib = lib;
  initialPkgsFor = pkgsFor;

  mkLibImpl =
    {
      lib,
      pkgsFor,
      outputs,
    }:
    import (src + "/lib.nix") {
      inherit
        inputs
        lib
        outputs
        pkgsFor
        src
        ;
    };

  self = rec {
    api = {
      version = exports.apiVersion;
      inherit (exports)
        constructorNames
        moduleSets
        ;
    };

    apiVersion = api.version;

    inherit (exports)
      constructorNames
      darwinModules
      homeModules
      moduleSets
      nixosModules
      ;

    modules = moduleSets;

    inherit (packageRegistry) packagePaths;
    packageNames = builtins.attrNames packagePaths;

    lintFiles = import (nixcfgPath "lint-files.nix");

    mkDevShell =
      {
        lib,
        gitHooks,
        lintFiles ? self.lintFiles,
        src ? repoSrc,
      }:
      import (nixcfgPath "dev-shell.nix") {
        inherit
          src
          gitHooks
          lib
          lintFiles
          ;
      };

    mkOverlay =
      {
        name ? "default",
        slib ? null,
      }:
      import (nixcfgPath "compat-overlay.nix") {
        inherit
          inputs
          name
          slib
          ;
        outputs = self;
      };

    overlays =
      (import (src + "/overlays/default.nix") {
        inherit inputs;
        outputs = self;
      })
      // {
        default = self.mkOverlay { };
      };

    mkPackages =
      {
        pkgs,
        system ? pkgs.stdenv.hostPlatform.system,
        inputsArg ? inputs,
        outputsArg ? self,
        extraPackageArgs ? { },
      }:
      let
        resolvedOutputs =
          if builtins.isAttrs outputsArg && outputsArg ? lib && outputsArg.lib != null then
            outputsArg
          else if self.lib != null then
            (if builtins.isAttrs outputsArg then outputsArg else { }) // { inherit (self) lib; }
          else
            throw "default.nix: mkPackages needs `outputsArg.lib` (or import default.nix with `lib` and `pkgsFor`).";
        packageSelfSource = import (nixcfgPath "package-self-source.nix") {
          inherit (resolvedOutputs) lib;
          outputs = resolvedOutputs;
        };
      in
      builtins.mapAttrs (
        name: path:
        pkgs.callPackage path (
          {
            inputs = inputsArg;
            outputs = resolvedOutputs;
          }
          // packageSelfSource.callPackageArgs name
          // extraPackageArgs
        )
      ) (packageRegistry.forSystem system);

    mkLib =
      {
        lib ? null,
        pkgsFor ? null,
        ...
      }@args:
      mkLibImpl (
        {
          lib =
            if lib != null then
              lib
            else if initialLib != null then
              initialLib
            else
              throw "default.nix: pass `lib` to mkLib (typically nixpkgs.lib).";
          pkgsFor =
            if pkgsFor != null then
              pkgsFor
            else if initialPkgsFor != null then
              initialPkgsFor
            else
              throw "default.nix: pass `pkgsFor` to mkLib (system -> pkgs attrset).";
          outputs = self;
        }
        // builtins.removeAttrs args [
          "lib"
          "pkgsFor"
        ]
      );

    lib =
      if initialLib != null && initialPkgsFor != null then
        mkLibImpl {
          lib = initialLib;
          pkgsFor = initialPkgsFor;
          outputs = self;
        }
      else
        null;

    constructors =
      if self.lib == null then
        { }
      else
        builtins.listToAttrs (
          builtins.map (name: {
            inherit name;
            value = builtins.getAttr name self.lib;
          }) exports.constructorNames
        );
  };
in
self
