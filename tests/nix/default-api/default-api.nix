{
  src ? ../../..,
}:
let
  exportedSystems = [
    "aarch64-darwin"
    "aarch64-linux"
    "x86_64-linux"
  ];
  packageSmokeSystem = "aarch64-darwin";

  flake = import (src + "/default.nix") {
    inherit src;
  };
  exports = import (src + "/lib/exports.nix") {
    inherit src;
  };
  moduleEntryPoint = import (src + "/modules") {
    inherit src;
  };
  registry = import (src + "/packages/registry.nix") {
    inherit src;
  };

  # Keep the minimal stub needed for shared selfSource injection semantics. The
  # contracts below exercise package wrapping, not nixpkgs startup cost.
  minimalInjectionLib = rec {
    setFunctionArgs = f: args: {
      __functor = _self: f;
      __functionArgs = args;
    };
    optionalAttrs = cond: attrs: if cond then attrs else { };
  };

  outputsArg = rec {
    lib = minimalInjectionLib // rec {
      sources = {
        wispr-flow = {
          version = "1.4.661";
        };
      };
      sourceEntry = name: sources.${name};
    };
  };

  fakePackageLib = {
    licenses = {
      unfree = "unfree";
    };
    platforms = {
      darwin = [
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      linux = [
        "aarch64-linux"
        "x86_64-linux"
      ];
    };
    sourceTypes = {
      binaryNativeCode = "binaryNativeCode";
    };
  };

  fakePkgsFor = system: {
    stdenv.hostPlatform.system = system;
    callPackage =
      pkg: args:
      let
        resolvedArgs = args // {
          mkDmgApp = attrs: attrs;
          lib = fakePackageLib;
        };
      in
      if builtins.isPath pkg then import pkg resolvedArgs else pkg resolvedArgs;
  };

  baseHelper = import (src + "/lib/package-materialization.nix") {
    inherit src;
  };
  helper = import (src + "/lib/package-materialization.nix") {
    inherit src;
    inherit (outputsArg) lib;
    outputs = outputsArg;
  };
  helperPackagesFor = system: helper.packageFunctionsForSystem system;
  mkPackagesFor =
    system:
    flake.mkPackages {
      pkgs = fakePkgsFor system;
      inherit outputsArg system;
    };
  mkPackagesOverride = flake.mkPackages {
    pkgs = fakePkgsFor packageSmokeSystem;
    system = packageSmokeSystem;
    inherit outputsArg;
    extraPackageArgs = {
      selfSource = {
        version = "override";
      };
    };
  };
  flakelightPackagesFor =
    system:
    import (src + "/packages/default.nix") {
      inherit system;
      inherit (outputsArg) lib;
      outputs = outputsArg;
    };

  selfSourceHelper = import (src + "/lib/package-self-source.nix") {
    lib = minimalInjectionLib;
    outputs = rec {
      lib = rec {
        sources.demo = {
          version = "1.2.3";
        };
        sourceEntry = name: sources.${name};
      };
    };
  };
  wrappedDemo = selfSourceHelper.injectIntoFunction "demo" (
    {
      selfSource,
      suffix ? "",
    }:
    selfSource.version + suffix
  );

  names = attrs: builtins.sort builtins.lessThan (builtins.attrNames attrs);
  systemChecks = builtins.concatLists (
    builtins.map (
      system:
      let
        helperPackages = helperPackagesFor system;
        mkPackages = mkPackagesFor system;
        flakelightPackages = flakelightPackagesFor system;
      in
      [
        (assertEq "registry names match helper names (${system})" (names (registry.forSystem system)) (
          names helperPackages
        ))
        (assertEq "helper names match mkPackages names (${system})" (names helperPackages) (
          names mkPackages
        ))
        (assertEq "helper names match flakelight names (${system})" (names helperPackages) (
          names flakelightPackages
        ))
      ]
    ) exportedSystems
  );
  stringifyModuleSet = builtins.mapAttrs (_: toString);
  stringifyModuleSets = builtins.mapAttrs (_: stringifyModuleSet);
  topLevelModuleSets = {
    inherit (flake) darwinModules homeModules nixosModules;
  };

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  checks = [
    (import ./constructors.nix { inherit src; })
    (assertEq "default api version symmetry" exports.api.version flake.api.version)
    (assertEq "default top-level api version symmetry" exports.api.version flake.apiVersion)
    (assertEq "default constructor names symmetry" exports.constructorNames flake.constructorNames)
    (assertEq "default module sets symmetry" (stringifyModuleSets exports.moduleSets) (
      stringifyModuleSets flake.api.moduleSets
    ))
    (assertEq "default top-level module aliases symmetry" (stringifyModuleSets exports.moduleSets) (
      stringifyModuleSets topLevelModuleSets
    ))
    (assertEq "default modules alias symmetry" (stringifyModuleSets exports.moduleSets) (
      stringifyModuleSets flake.modules
    ))
    (assertEq "default module entrypoint symmetry" (stringifyModuleSets exports.moduleSets) (
      stringifyModuleSets moduleEntryPoint
    ))
    (assertEq "mkPackages injects selfSource" "1.4.661"
      (mkPackagesFor packageSmokeSystem).wispr-flow.info.version
    )
    (assertEq "mkPackages preserves explicit selfSource overrides" "override"
      mkPackagesOverride.wispr-flow.info.version
    )
    (assertEq "registry names match base helper names" (names registry.packagePaths)
      baseHelper.packageNames
    )
    (assertEq "registry names match base helper paths" (names registry.packagePaths) (
      names baseHelper.packagePaths
    ))
    (assertEq "registry names match flake package names" (names registry.packagePaths)
      flake.packageNames
    )
    (assertEq "registry names match flake package paths" (names registry.packagePaths) (
      names flake.packagePaths
    ))
    (assertEq "registry wispr path matches base helper" (toString registry.packagePaths.wispr-flow) (
      toString baseHelper.packagePaths.wispr-flow
    ))
    (assertEq "registry wispr path matches flake package path"
      (toString registry.packagePaths.wispr-flow)
      (toString flake.packagePaths.wispr-flow)
    )
    (assertEq "helper wispr version" "1.4.661"
      ((helperPackagesFor packageSmokeSystem).wispr-flow {
        mkDmgApp = attrs: attrs;
        lib = fakePackageLib;
      }).info.version
    )
    (assertEq "mkPackages wispr version" "1.4.661"
      (mkPackagesFor packageSmokeSystem).wispr-flow.info.version
    )
    (assertEq "flakelight wispr version" "1.4.661"
      ((flakelightPackagesFor packageSmokeSystem).wispr-flow {
        mkDmgApp = attrs: attrs;
        lib = fakePackageLib;
      }).info.version
    )
    (assertEq "selfSource helper callPackage path" "1.2.3"
      (selfSourceHelper.callPackageArgs "demo").selfSource.version
    )
    (assertEq "selfSource helper wrapped default" "1.2.3-ok" (wrappedDemo {
      suffix = "-ok";
    }))
    (assertEq "selfSource helper wrapped override" "override" (wrappedDemo {
      selfSource = {
        version = "override";
      };
    }))
  ]
  ++ systemChecks;
in
builtins.deepSeq checks true
