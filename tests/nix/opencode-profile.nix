{
  lib,
  src ? ../..,
}:
let
  homeLib = lib // {
    hm.dag.entryAfter = after: data: { inherit after data; };
  };
  homeForConfig =
    {
      activeProfile,
      enable,
      mcpServers ? { },
      profiles ? {
        personal = { };
        work = { };
      },
    }:
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
                name = "fixture-theme";
                slug = "catppuccin-frappe";
              };
            };
            programs.opencode = {
              enable = lib.mkOption {
                type = lib.types.bool;
                default = false;
              };
              settings = lib.mkOption {
                type = lib.types.attrsOf lib.types.anything;
                default = { };
              };
              tui = lib.mkOption {
                type = lib.types.attrsOf lib.types.anything;
                default = { };
              };
            };
            xdg = {
              configHome = lib.mkOption {
                type = lib.types.str;
                default = "/Users/Profile Test/.config";
              };
              configFile = lib.mkOption {
                type = lib.types.attrsOf (
                  lib.types.submodule {
                    options.text = lib.mkOption { type = lib.types.str; };
                  }
                );
                default = { };
              };
            };
            assertions = lib.mkOption {
              type = lib.types.listOf lib.types.attrs;
              default = [ ];
            };
          };
          config.nixcfg.opencode = {
            inherit
              activeProfile
              enable
              mcpServers
              profiles
              ;
          };
        }
      ];
    }).config;
  homeFor = activeProfile: enable: homeForConfig { inherit activeProfile enable; };
  darwinFor =
    home:
    (lib.evalModules {
      specialArgs = {
        primaryUser = "alice";
        # base.nix reads pkgs.zsh for environment.shells and the user shell, and
        # pkgs.stdenv.hostPlatform.isDarwin to gate the Homebrew module.
        pkgs = {
          zsh = "zsh";
          stdenv.hostPlatform.isDarwin = true;
        };
      };
      modules = [
        (src + "/modules/darwin/base.nix")
        {
          options =
            lib.genAttrs [
              "environment"
              "system"
              "users"
              "networking"
              "security"
              "programs"
              "launchd"
              "homebrew"
              "home-manager"
              "nix-homebrew"
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
  inherit (import ./lib.nix) assertEq;
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
  mcpHome = homeForConfig {
    activeProfile = "personal";
    enable = true;
    mcpServers.demo = {
      command = [ "demo" ];
      enable = true;
    };
    profiles.personal.mcpServers.demo = {
      command = null;
      type = "remote";
      url = "https://example.test/mcp";
    };
  };
  mcpProfileJson = builtins.fromJSON mcpHome.xdg.configFile."opencode/personal.json".text;
  nullableTypeHome = homeForConfig {
    activeProfile = "personal";
    enable = true;
    mcpServers.demo = {
      command = [ "demo" ];
      enable = true;
    };
    profiles.personal.mcpServers.demo.type = null;
  };
  nullableTypeProfileJson =
    builtins.fromJSON
      nullableTypeHome.xdg.configFile."opencode/personal.json".text;
  nullableTypeFailures = builtins.filter (
    assertion: !assertion.assertion
  ) nullableTypeHome.assertions;
  disabledHome = homeFor "personal" false;
  disabledDarwin = darwinFor disabledHome;
  absentDarwin = darwinFor { };
  fallback = compatibilityFor { };
  absentHomeFallback = compatibilityFor { specialArgs.primaryUser = "alice"; };
  checks = profileChecks ++ [
    (assertEq "base MCP JSON retains enabled state" {
      command = [ "demo" ];
      enabled = true;
      type = "local";
    } mcpHome.programs.opencode.settings.mcp.demo)
    (assertEq "profile MCP JSON switches transport without overriding enabled" {
      command = null;
      type = "remote";
      url = "https://example.test/mcp";
    } mcpProfileJson.mcp.demo)
    (assertEq "profile transport switch inherits enabled through sparse layering" false (
      mcpProfileJson.mcp.demo ? enabled
    ))
    (assertEq "resolved MCP assertions pass" [ ] (
      builtins.filter (assertion: !assertion.assertion) mcpHome.assertions
    ))
    (assertEq "module accepts a sparse nullable type for resolved validation" {
      type = null;
    } nullableTypeProfileJson.mcp.demo)
    (assertEq "module resolved assertions reject nullable type independently" [
      "nixcfg.opencode.profiles.personal.mcpServers.demo: type must be either \"local\" or \"remote\"."
    ] (builtins.map (assertion: assertion.message) nullableTypeFailures))
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
