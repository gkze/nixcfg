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
    apps = [ "/System/Applications/Messages.app" ];
    inherit homeDirectory;
    options = {
      home.activation = { };
      system.defaults.dock = { };
    };
    pkgs.dockutil = "/nix/store/test-dockutil";
  };
  persistentOthers = (builtins.elemAt module.contents 0).system.defaults.dock.persistent-others;
  activation = (builtins.elemAt module.contents 1).home.activation.testDock;

  # Structural checks on the rendered activation. Splitting on the reset command
  # proves ordering and guarding; a substring test would pass even if the reset
  # ran after the adds or outside the availability guard.
  clearMarker = ''"$dockutil" --remove all --no-restart'';
  clearParts = lib.splitString clearMarker activation;
  beforeClear = builtins.elemAt clearParts 0;
  afterClear = builtins.elemAt clearParts 1;
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
    (assertEq "the Dock reset appears exactly once" 1 (builtins.length clearParts - 1))
    (assertEq "the reset is the condition of a negated dockutil test" true (
      lib.hasSuffix "if ! " beforeClear
    ))
    (assertEq "the reset precedes every add" false (lib.hasInfix "--add" beforeClear))
    (assertEq "the reset failure branch is fatal" true (
      lib.hasInfix "failed to clear Dock items" afterClear
    ))
    (assertEq "the declared app is added after the reset" true (
      lib.hasInfix "/System/Applications/Messages.app" afterClear
    ))
  ];
in
builtins.deepSeq checks true
