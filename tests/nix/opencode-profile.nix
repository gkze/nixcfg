{
  lib,
  src ? ../..,
}:
let
  homeLib = lib // {
    hm.dag.entryAfter = after: data: { inherit after data; };
  };
  homeFor =
    activeProfile: enable:
    (lib.evalModules {
      specialArgs.lib = homeLib;
      modules = [
        (src + "/modules/home/opencode.nix")
        {
          options = {
            home = lib.mkOption {
              type = lib.types.submodule {
                freeformType = lib.types.attrsOf lib.types.anything;
                options.homeDirectory = lib.mkOption {
                  type = lib.types.str;
                  default = "/Users/Profile Test";
                };
              };
            };
            theme = lib.mkOption {
              default = {
                slug = "catppuccin-frappe";
              };
            };
            programs = lib.mkOption { type = lib.types.attrs; };
            xdg = lib.mkOption {
              type = lib.types.attrs;
              default = { };
            };
            assertions = lib.mkOption {
              type = lib.types.listOf lib.types.attrs;
              default = [ ];
            };
          };
          config.nixcfg.opencode = {
            inherit activeProfile enable;
            profiles = {
              personal = { };
              work = { };
            };
          };
        }
      ];
    }).config;
  darwinFor =
    home:
    (lib.evalModules {
      specialArgs = {
        primaryUser = "alice";
        pkgs = { };
      };
      modules = [
        (src + "/modules/darwin/base.nix")
        {
          options =
            lib.genAttrs [
              "system"
              "users"
              "networking"
              "security"
              "programs"
              "launchd"
              "homebrew"
              "home-manager"
            ] (_: lib.mkOption { type = lib.types.attrs; })
            // {
              assertions = lib.mkOption { type = lib.types.listOf lib.types.attrs; };
            };
          config.home-manager.users.alice = home;
        }
      ];
    }).config;
  slib = import (src + "/lib/lib.nix") {
    inherit src lib;
    inputs = { };
    outputs = { };
    pkgsFor = { };
  };
  compatibilityFor =
    {
      configName ? "personal.json",
      specialArgs ? { },
      homeManager ? { },
    }:
    (lib.evalModules {
      inherit specialArgs;
      modules = [
        (slib.mkSetOpencodeEnvModule configName)
        {
          options = lib.genAttrs [ "launchd" "home-manager" ] (_: lib.mkOption { type = lib.types.attrs; });
          config.home-manager = homeManager;
        }
      ];
    }).config.launchd.user.agents.set-opencode-env;
  assertEq =
    label: expected: actual:
    if expected == actual then
      true
    else
      throw "${label}: expected ${builtins.toJSON expected}, got ${builtins.toJSON actual}";
  profileChecks =
    lib.concatMap
      (
        profile:
        let
          home = homeFor profile true;
          darwin = darwinFor home;
          path = "/Users/Profile Test/.config/opencode/${profile}.json";
          compatibility = compatibilityFor {
            configName = "stale.json";
            specialArgs.primaryUser = "alice";
            homeManager.users.alice = home;
          };
        in
        [
          (assertEq "shell selects ${profile}" path home.home.sessionVariables.OPENCODE_CONFIG)
          (assertEq "launchd selects ${profile}" path darwin.launchd.user.envVariables.OPENCODE_CONFIG)
          (assertEq "selected ${profile} file exists" true (home.xdg.configFile ? "opencode/${profile}.json"))
          (assertEq "legacy constructor honors ${profile}" ''
            launchctl setenv OPENCODE_CONFIG ${lib.escapeShellArg path}
          '' compatibility.script)
        ]
      )
      [
        "personal"
        "work"
      ];
  disabledHome = homeFor "personal" false;
  disabledDarwin = darwinFor disabledHome;
  absentDarwin = darwinFor { };
  fallback = compatibilityFor { };
  absentHomeFallback = compatibilityFor { specialArgs.primaryUser = "alice"; };
  checks = profileChecks ++ [
    (assertEq "disabled OpenCode has no GUI override" false (
      disabledDarwin.launchd.user.envVariables ? OPENCODE_CONFIG
    ))
    (assertEq "absent Home Manager has no GUI override" false (
      absentDarwin.launchd.user.envVariables ? OPENCODE_CONFIG
    ))
    (assertEq "legacy standalone path remains supported" ''
      launchctl setenv OPENCODE_CONFIG "$HOME"/.config/opencode/personal.json
    '' fallback.script)
    (assertEq "legacy constructor tolerates absent Home Manager" ''
      launchctl setenv OPENCODE_CONFIG "$HOME"/.config/opencode/personal.json
    '' absentHomeFallback.script)
  ];
in
# Native module merging must agree across Home Manager and nix-darwin consumers.
builtins.deepSeq checks true
