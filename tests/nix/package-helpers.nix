{
  src ? ../..,
}:
let
  fixtures = ./package-helpers;

  discovery = import (src + "/lib/discovery.nix");
  mkGoCli = import (src + "/lib/go-cli-package.nix") {
    mkGoCliPackage = args: args;
    inputs.demo = "demo-source";
    lib.licenses = {
      mit = "MIT";
      asl20 = "Apache-2.0";
    };
  };

  inherit (import ./lib.nix) assertEq;

  sidecars = discovery.discoverSidecarEntries {
    root = fixtures + "/sidecars";
    fileName = "sources.json";
  };
  missingSidecars = discovery.discoverSidecarEntries {
    root = fixtures + "/missing";
    fileName = "sources.json";
  };
  duplicateSidecars =
    builtins.tryEval
      (discovery.discoverSidecarEntries {
        root = fixtures + "/duplicates";
        fileName = "sources.json";
      }).names;

  packageCollision =
    builtins.tryEval
      (discovery.discoverDefaultNixEntries {
        root = fixtures + "/collisions/packages";
        excludeFiles = [
          "default.nix"
          "registry.nix"
        ];
      }).names;
  overlayCollision =
    builtins.tryEval
      (discovery.discoverDefaultNixEntries {
        root = fixtures + "/collisions/overlays";
        excludeFiles = [ "default.nix" ];
      }).fileNames;
  currentOverlayEntries = discovery.discoverDefaultNixEntries {
    root = src + "/overlays";
    excludeFiles = [ "default.nix" ];
  };

  registry = import (src + "/packages/registry.nix") { inherit src; };
  ordinaryEntries = discovery.discoverDefaultNixEntries {
    root = fixtures + "/registry/ordinary";
  };
  companionEntries = discovery.discoverCompanionEntries {
    root = fixtures + "/registry/ordinary";
    directories = ordinaryEntries.dirNames;
    fileName = "crate2nix-src.nix";
  };
  flatEntries = discovery.discoverDefaultNixEntries {
    root = fixtures + "/registry/flat";
  };
  metadataForNames =
    names:
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value = { };
      }) names
    );
  overridesForNames =
    names:
    builtins.listToAttrs (
      builtins.map (name: {
        inherit name;
        value.constraint = "darwin";
      }) names
    );
  mkOverrideCase =
    names: staleName:
    let
      baseMetadata = metadataForNames names;
      overrides = overridesForNames names;
    in
    {
      inherit overrides;
      known = registry.validatePackageMetadataOverrides baseMetadata overrides;
      unknown = builtins.tryEval (
        registry.validatePackageMetadataOverrides baseMetadata (overrides // { ${staleName} = { }; })
      );
    };
  ordinaryOverrideCase = mkOverrideCase ordinaryEntries.names "stale-ordinary";
  companionOverrideCase = mkOverrideCase companionEntries.names "stale-companion";
  flatOverrideCase = mkOverrideCase flatEntries.names "stale-flat";

  pathBasenames = builtins.mapAttrs (_: path: builtins.baseNameOf (toString path));

  goCliPackage = mkGoCli {
    pname = "demo";
    cmdName = "demo";
    description = "Demo CLI";
    homepage = "https://example.test/demo";
    license = "Apache-2.0";
    meta.mainProgram = "demo";
  };
  goCliMeta = goCliPackage.meta;

  goCliDefaults = {
    inherit (goCliPackage) input subPackages;
  };

  defaultGo = {
    version = "1.26.0";
  };
  selectedGo = {
    version = "1.27.4";
  };
  mkBuildGoModule = go: {
    __functor = _: args: args // { builtWithGoVersion = go.version; };
    override = { go }: mkBuildGoModule go;
  };
  goHelpers = import (src + "/overlays/_lib/helpers/go.nix") {
    final = { };
    inputs = { };
    prev = {
      go = defaultGo;
      buildGoModule = mkBuildGoModule defaultGo;
      installShellFiles = "install-shell-files";
    };
    slib.sourceHash = _: _: "sha256-fixture";
  };
  goCliWithToolchainContract = goHelpers.mkGoCliPackage {
    pname = "demo";
    input = "demo-source";
    subPackages = [ "cmd/demo" ];
    version = "1.2.3";
    go = selectedGo;
    passthru = {
      existing = "preserved";
      goVersion = "caller-override";
    };
  };

  checks = [
    (assertEq "sidecar names" [
      "alpha"
      "beta"
    ] sidecars.names)
    (assertEq "sidecar paths" {
      alpha = "sources.json";
      beta = "beta.sources.json";
    } (pathBasenames sidecars.entries))
    (assertEq "missing sidecar names" [ ] missingSidecars.names)
    (assertEq "missing sidecar entries" { } missingSidecars.entries)
    (assertEq "duplicate sidecar discovery fails" false duplicateSidecars.success)
    (assertEq "duplicate sidecar discovery value" false duplicateSidecars.value)
    (assertEq "package default.nix collision fails" false packageCollision.success)
    (assertEq "package default.nix collision value" false packageCollision.value)
    (assertEq "overlay default.nix collision fails" false overlayCollision.success)
    (assertEq "overlay default.nix collision value" false overlayCollision.value)
    (assertEq "current overlay discovery has no flat/directory collisions" true (
      builtins.deepSeq currentOverlayEntries.names true
    ))
    (assertEq "current registry overrides all resolve" true (
      builtins.deepSeq (builtins.attrNames registry.packagePaths) true
    ))
    (assertEq "ordinary registry fixture" [ "demo" ] ordinaryEntries.names)
    (assertEq "companion registry fixture" [ "demo-crate2nix-src" ] companionEntries.names)
    (assertEq "flat-file registry fixture" [ "demo" ] flatEntries.names)
    (assertEq "known ordinary override is preserved" ordinaryOverrideCase.overrides
      ordinaryOverrideCase.known
    )
    (assertEq "unknown ordinary override fails" false ordinaryOverrideCase.unknown.success)
    (assertEq "known companion override is preserved" companionOverrideCase.overrides
      companionOverrideCase.known
    )
    (assertEq "unknown companion override fails" false companionOverrideCase.unknown.success)
    (assertEq "known flat-file override is preserved" flatOverrideCase.overrides flatOverrideCase.known)
    (assertEq "unknown flat-file override fails" false flatOverrideCase.unknown.success)
    (assertEq "go cli defaults" {
      input = "demo-source";
      subPackages = [ "cmd/demo" ];
    } goCliDefaults)
    (assertEq "go cli metadata merge" {
      description = "Demo CLI";
      homepage = "https://example.test/demo";
      license = "Apache-2.0";
      mainProgram = "demo";
    } goCliMeta)
    (assertEq "go cli selected toolchain" "1.27.4" goCliWithToolchainContract.builtWithGoVersion)
    (assertEq "go cli toolchain passthru" {
      existing = "preserved";
      goVersion = "1.27.4";
    } goCliWithToolchainContract.passthru)
  ];
in
builtins.deepSeq checks true
