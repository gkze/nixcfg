# The two Dock declarations must route managed bundles through the resolved
# application path. A literal `/Applications` path would bypass the gc
# mitigation `dock-lib.nix` exists to provide, and nothing else would notice:
# the host still evaluates and the Dock still renders.
#
# The property is asserted on the evaluated declaration, not on its source
# text, so adding or removing an app never touches this test.
{
  lib,
  src ? ../../..,
}:
let
  inherit (import ../lib.nix) assertEq;

  homeDirectory = "/Users/test";

  # Stub the Home Manager dager, and collapse `mkMerge` into a plain union so
  # the composed module can be read directly instead of through a merge node.
  dockLib = lib // {
    hm.dag.entryAfter = _after: text: text;
    mkMerge = builtins.foldl' (acc: value: acc // value) { };
  };

  # Compose a declaration against a stub host. Nothing is resolved in
  # `nixcfg.macApps.resolved`, so every `appPath` falls back to the per-user
  # Applications directory, which is what makes the two path shapes distinct.
  appsFor =
    declaration:
    let
      module = import (src + ("/darwin/george/" + declaration)) {
        lib = dockLib;
        config.home = { inherit homeDirectory; };
        options = {
          home.activation = { };
          system.defaults.dock = { };
        };
        pkgs.dockutil = "/nix/store/test-dockutil";
        primaryUser = "test";
      };
    in
    map (entry: entry.app) module.system.defaults.dock.persistent-apps;

  isSystemApp = app: lib.hasPrefix "/System/Applications/" app;
  isResolvedBundle = app: lib.hasPrefix "${homeDirectory}/Applications/" app;

  checkDeclaration =
    declaration:
    let
      apps = appsFor declaration;
      unresolved = builtins.filter (app: !(isSystemApp app || isResolvedBundle app)) apps;
    in
    [
      (assertEq "${declaration}: declares at least one app" true (apps != [ ]))
      (assertEq "${declaration}: every app is a system app or a resolved bundle" [ ] unresolved)
    ];
in
builtins.deepSeq (checkDeclaration "dock-apps.nix" ++ checkDeclaration "town-dock-apps.nix") true
