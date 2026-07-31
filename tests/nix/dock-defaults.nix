{
  lib,
  src ? ../..,
}:
let
  dock = import (src + "/modules/darwin/george/dock-lib.nix") {
    lib = lib // {
      hm.dag.entryAfter = _after: script: script;
    };
  };

  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";

  homeDirectory = "/Users/test";
  module = dock.mkDockModule {
    activationName = "testDock";
    apps = [ ];
    inherit homeDirectory;
    options = {
      home.activation = { };
      system.defaults.dock = { };
    };
    pkgs.dockutil = "/nix/store/test-dockutil";
  };
  persistentOthers = (builtins.elemAt module.contents 0).system.defaults.dock.persistent-others;
  activation = (builtins.elemAt module.contents 1).home.activation.testDock;
  checks = [
    (assertEq "standard Dock others" [
      {
        folder = {
          path = "/Applications";
          arrangement = "name";
        };
      }
      {
        folder = {
          path = "/Applications/Utilities";
          arrangement = "name";
        };
      }
      {
        folder = {
          path = "${homeDirectory}/Downloads";
          arrangement = "date-modified";
        };
      }
    ] persistentOthers)
    (assertEq "standard stale Dock others are removed" true (
      lib.hasInfix "${homeDirectory}/Applications" activation
    ))
  ];
in
builtins.deepSeq checks true
