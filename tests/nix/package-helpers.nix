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

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

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
  ];
in
builtins.deepSeq checks true
