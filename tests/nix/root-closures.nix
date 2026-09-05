{
  actualManifest,
  lib,
  src,
}:
let
  fakeClosure = system: outPath: {
    inherit outPath system;
  };
  inventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-linux"
    ];
    darwinConfigurations.workstation.system = fakeClosure "aarch64-darwin" "/nix/store/darwin-system";
    nixosConfigurations.server.config.system.build.toplevel =
      fakeClosure "x86_64-linux" "/nix/store/nixos-system";
    homeConfigurations = {
      alice.activationPackage = fakeClosure "x86_64-linux" "/nix/store/home-alice";
      george.activationPackage = fakeClosure "aarch64-darwin" "/nix/store/home-george";
    };
    requiredKinds = [
      "darwin"
      "nixos"
      "home"
    ];
    requiredRoots = [
      {
        kind = "darwin";
        name = "workstation";
      }
      {
        kind = "home";
        name = "george";
      }
    ];
  };
  invalidInventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [ "aarch64-darwin" ];
    nixosConfigurations.server.config.system.build.toplevel =
      fakeClosure "x86_64-linux" "/nix/store/unsupported-system";
  };
  missingRequiredInventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [ "aarch64-darwin" ];
    requiredKinds = [ "home" ];
  };
  missingNamedRootInventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [ "aarch64-darwin" ];
    darwinConfigurations.workstation.system = fakeClosure "aarch64-darwin" "/nix/store/darwin-system";
    requiredKinds = [ "darwin" ];
    requiredRoots = [
      {
        kind = "darwin";
        name = "missing";
      }
    ];
  };
in
assert
  inventory.manifest == {
    schemaVersion = 2;
    requiredKinds = [
      "darwin"
      "nixos"
      "home"
    ];
    requiredRoots = [
      {
        kind = "darwin";
        name = "workstation";
      }
      {
        kind = "home";
        name = "george";
      }
    ];
    roots = [
      {
        kind = "darwin";
        name = "workstation";
        system = "aarch64-darwin";
      }
      {
        kind = "nixos";
        name = "server";
        system = "x86_64-linux";
      }
      {
        kind = "home";
        name = "alice";
        system = "x86_64-linux";
      }
      {
        kind = "home";
        name = "george";
        system = "aarch64-darwin";
      }
    ];
  };
assert
  inventory.rootSystems == [
    "aarch64-darwin"
    "x86_64-linux"
  ];
assert
  map (root: root.name) (inventory.forSystem "aarch64-darwin") == [
    "darwin-workstation"
    "home-george"
  ];
assert
  map (root: root.path.outPath) (inventory.forSystem "x86_64-linux") == [
    "/nix/store/nixos-system"
    "/nix/store/home-alice"
  ];
assert (inventory.forSystem "aarch64-linux") == [ ];
assert !(builtins.tryEval invalidInventory.manifest).success;
assert !(builtins.tryEval missingRequiredInventory.manifest).success;
assert !(builtins.tryEval missingNamedRootInventory.manifest).success;
assert actualManifest.schemaVersion == 2;
assert
  actualManifest.requiredKinds == [
    "darwin"
    "home"
  ];
assert builtins.all (
  kind: builtins.any (root: root.kind == kind) actualManifest.roots
) actualManifest.requiredKinds;
assert builtins.all (
  required:
  builtins.any (root: root.kind == required.kind && root.name == required.name) actualManifest.roots
) actualManifest.requiredRoots;
true
