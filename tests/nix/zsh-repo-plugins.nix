{
  lib,
  pkgs,
  src ? ../..,
}:
let
  mkTestSource =
    unrelatedFile:
    lib.fileset.toSource {
      root = src;
      fileset = lib.fileset.unions [
        (src + "/misc/zsh-plugins")
        (src + "/modules/home/gpg-tty.zsh")
        (src + "/modules/home/zsh.nix")
        (src + "/${unrelatedFile}")
      ];
    };

  sourceA = mkTestSource "default.nix";
  sourceB = mkTestSource "flake.nix";

  evalPlugins =
    source:
    let
      evaluated = lib.evalModules {
        specialArgs = {
          pkgs = {
            zsh-autosuggestions = source;
            zsh-f-sy-h = source;
            zsh-vi-mode = source;
            zsh-fzf-history-search = source;
            zsh-system-clipboard = source;
          };
          slib.srcDirBase = _system: "/work";
          src = source;
          system = "x86_64-linux";
        };
        modules = [
          (source + "/modules/home/zsh.nix")
          (
            { lib, ... }:
            {
              options = {
                programs.zsh = lib.mkOption {
                  type = lib.types.attrsOf lib.types.anything;
                  default = { };
                };
                xdg.configHome = lib.mkOption { type = lib.types.str; };
              };
              config = {
                nixcfg.zsh = {
                  includeAwsCompletion = false;
                  includeDockerCompletion = false;
                };
                xdg.configHome = "/home/test/.config";
              };
            }
          )
        ];
      };
    in
    builtins.filter (
      plugin: lib.hasPrefix "zsh-vi-mode-" plugin.name && plugin.name != "zsh-vi-mode"
    ) evaluated.config.programs.zsh.plugins;

  repoPluginsA = evalPlugins sourceA;
  repoPluginsB = evalPlugins sourceB;
  pluginNames = plugins: builtins.map (plugin: plugin.name) plugins;
  repoPluginsSourceA = (builtins.head repoPluginsA).src;
  pluginSources = plugins: builtins.map (plugin: toString plugin.src) plugins;
  expectedNames = [
    "zsh-vi-mode-backward-kill-word"
    "zsh-vi-mode-system-clipboard"
  ];
  checks = [
    (
      assert pluginNames repoPluginsA == expectedNames;
      true
    )
    (
      assert pluginNames repoPluginsB == expectedNames;
      true
    )
    (
      assert builtins.length (lib.unique (pluginSources repoPluginsA)) == 1;
      true
    )
    # An unrelated repository edit must not change either plugin's source
    # identity in the evaluated Home Manager configuration.
    (
      assert pluginSources repoPluginsA == pluginSources repoPluginsB;
      true
    )
  ];
in
assert builtins.deepSeq checks true;
pkgs.runCommand "test-nix-zsh-repo-plugins" { } ''
  test -f ${sourceA}/modules/home/zsh.nix
  test -f ${sourceB}/modules/home/zsh.nix
  test -f ${repoPluginsSourceA}/zsh-vi-mode-backward-kill-word.plugin.zsh
  test -f ${repoPluginsSourceA}/zsh-vi-mode-system-clipboard.plugin.zsh
  actual_files="$(
    find ${repoPluginsSourceA} -mindepth 1 -maxdepth 1 -type f -exec basename {} \; \
      | LC_ALL=C sort
  )"
  expected_files="$(printf '%s\n' \
    zsh-vi-mode-backward-kill-word.plugin.zsh \
    zsh-vi-mode-system-clipboard.plugin.zsh)"
  test "$actual_files" = "$expected_files"
  touch "$out"
''
