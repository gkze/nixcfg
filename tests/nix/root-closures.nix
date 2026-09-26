{
  actualManifest,
  flakelight,
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
  declaredInventory =
    closure:
    import (src + "/lib/root-closures.nix") {
      inherit lib;
      systems = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
      declaredSystems.darwin.workstation = "aarch64-darwin";
      darwinConfigurations.workstation.system = closure;
      requiredKinds = [ "darwin" ];
    };
  lazyInventory = declaredInventory (throw "root discovery forced a closure");
  mismatchedInventory = declaredInventory (fakeClosure "x86_64-linux" "/nix/store/wrong-system");
  matchingInventory = declaredInventory (fakeClosure "aarch64-darwin" "/nix/store/right-system");
  lazyConfigurationsInventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [ "aarch64-darwin" ];
    declaredSystems.darwin.workstation = "aarch64-darwin";
    darwinConfigurations = throw "root discovery forced configuration names";
  };
  missingDeclaredRootInventory = import (src + "/lib/root-closures.nix") {
    inherit lib;
    systems = [ "aarch64-darwin" ];
    declaredSystems.darwin.workstation = "aarch64-darwin";
  };
in
assert import ./flakelight-darwin.nix { inherit lib flakelight src; };
assert lazyInventory.rootSystems == [ "aarch64-darwin" ];
assert lazyConfigurationsInventory.rootSystems == [ "aarch64-darwin" ];
assert !(builtins.tryEval lazyConfigurationsInventory.manifest).success;
assert !(builtins.tryEval missingDeclaredRootInventory.manifest).success;
assert !(builtins.tryEval (missingDeclaredRootInventory.forSystem "aarch64-darwin")).success;
assert
  lazyInventory.manifest.roots == [
    {
      kind = "darwin";
      name = "workstation";
      system = "aarch64-darwin";
    }
  ];
assert !(builtins.tryEval (lazyInventory.forSystem "aarch64-darwin")).success;
assert mismatchedInventory.rootSystems == [ "aarch64-darwin" ];
assert
  mismatchedInventory.manifest.roots == [
    {
      kind = "darwin";
      name = "workstation";
      system = "aarch64-darwin";
    }
  ];
assert !(builtins.tryEval (mismatchedInventory.forSystem "aarch64-darwin")).success;
assert !(builtins.tryEval (mismatchedInventory.forSystem "x86_64-linux")).success;
assert
  matchingInventory.manifest.roots == [
    {
      kind = "darwin";
      name = "workstation";
      system = "aarch64-darwin";
    }
  ];
assert
  (builtins.head (matchingInventory.forSystem "aarch64-darwin")).path.outPath
  == "/nix/store/right-system";
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
