{
  src ? ../../..,
}:
let
  # Keep this stub intentionally tiny: the check only needs enough of nixpkgs
  # lib for `default.nix` to materialize `self.lib` and derive the exported
  # constructors attrset. Using the full pinned nixpkgs import would turn this
  # into a startup/cache test instead of a structural API check.
  fakeLib = rec {
    lists = {
      findFirst =
        pred: default: list:
        let
          len = builtins.length list;
          go =
            i:
            if i >= len then
              default
            else
              let
                item = builtins.elemAt list i;
              in
              if pred item then item else go (i + 1);
        in
        go 0;

      optionals = cond: xs: if cond then xs else [ ];
    };

    attrsets.optionalAttrs = cond: attrs: if cond then attrs else { };

    range = start: end: if start > end then [ ] else builtins.genList (i: start + i) (end - start + 1);

    concatMapStringsSep =
      sep: f: xs:
      builtins.concatStringsSep sep (builtins.map f xs);

    mkDefault = x: x;
    mkForce = x: x;
    mkImageMediaOverride = x: x;

    genAttrs =
      names: f:
      builtins.listToAttrs (
        builtins.map (name: {
          inherit name;
          value = f name;
        }) names
      );
  };

  flake = import (src + "/default.nix") {
    inherit src;
    inputs = {
      nix-homebrew.darwinModules.nix-homebrew = "nix-homebrew-module";
      nix-rosetta-builder.darwinModules.default = "rosetta-builder-module";
    };
    lib = fakeLib;
    pkgsFor = { };
  };

  expected = builtins.sort builtins.lessThan flake.constructorNames;
  actual = builtins.sort builtins.lessThan (builtins.attrNames flake.constructors);
  nonFunctions = builtins.filter (
    name: !(builtins.isFunction (builtins.getAttr name flake.constructors))
  ) expected;
  emptyDarwinUsers = builtins.tryEval (
    flake.lib.mkSystem {
      system = "aarch64-darwin";
      users = [ ];
    }
  );
  emptyLinuxPrimaryUser =
    (flake.lib.mkSystem {
      system = "x86_64-linux";
      users = [ ];
    }).specialArgs.primaryUser;
  contextualLib = flake.mkLib {
    evaluationContext.sourceOverrides.context-demo = {
      version = "explicit";
      hashes = [ ];
    };
  };
  defaultDarwinModules =
    (flake.lib.mkDarwinHost {
      user = "alice";
      includeDefaultUserModule = false;
    }).modules;
  noRosettaDarwinModules =
    (flake.lib.mkDarwinHost {
      user = "alice";
      includeDefaultUserModule = false;
      enableRosettaBuilder = false;
    }).modules;
in
if expected != actual then
  throw "default.nix constructors mismatch: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}"
else if nonFunctions != [ ] then
  throw "default.nix constructors not functions: ${builtins.toJSON nonFunctions}"
else if emptyDarwinUsers.success then
  throw "mkSystem must reject a Darwin system without a primary user"
else if emptyLinuxPrimaryUser != null then
  throw "mkSystem must preserve userless NixOS support"
else if (contextualLib.sourceEntry "context-demo").version != "explicit" then
  throw "mkLib did not apply its explicit evaluation context"
else if !(builtins.elem "rosetta-builder-module" defaultDarwinModules) then
  throw "mkDarwinHost must enable its Rosetta builder by default"
else if builtins.elem "rosetta-builder-module" noRosettaDarwinModules then
  throw "mkDarwinHost must allow callers to disable its Rosetta builder"
else
  true
