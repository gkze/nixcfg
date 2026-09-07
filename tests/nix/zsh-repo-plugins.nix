{
  lib,
  pkgs,
  src ? ../..,
}:
let
  pluginRoot = src + "/misc/zsh-plugins";
  expectedFiles = [
    (pluginRoot + "/zsh-vi-mode-backward-kill-word.plugin.zsh")
    (pluginRoot + "/zsh-vi-mode-system-clipboard.plugin.zsh")
  ];
  # Observe the module's real projection boundary before materializing it.
  # A plugin-only root and fileset keep unrelated repository edits out of its
  # source identity without importing a newly projected tree during evaluation.
  moduleLib = lib // {
    fileset = lib.fileset // {
      toSource =
        { root, fileset }@args:
        assert root == pluginRoot;
        assert lib.fileset.toList fileset == expectedFiles;
        lib.fileset.toSource args;
    };
  };

  evalPlugins =
    source:
    let
      evaluated = lib.evalModules {
        specialArgs = {
          lib = moduleLib;
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
          (src + "/modules/home/zsh.nix")
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

  repoPlugins = evalPlugins src;
  pluginNames = plugins: builtins.map (plugin: plugin.name) plugins;
  repoPluginsSource = (builtins.head repoPlugins).src;
  pluginSources = plugins: builtins.map (plugin: toString plugin.src) plugins;
  expectedNames = [
    "zsh-vi-mode-backward-kill-word"
    "zsh-vi-mode-system-clipboard"
  ];
  checks = [
    (
      assert pluginNames repoPlugins == expectedNames;
      true
    )
    (
      assert builtins.length (lib.unique (pluginSources repoPlugins)) == 1;
      true
    )
  ];
in
assert builtins.deepSeq checks true;
pkgs.runCommand "test-nix-zsh-repo-plugins" { } ''
  test -f ${repoPluginsSource}/zsh-vi-mode-backward-kill-word.plugin.zsh
  test -f ${repoPluginsSource}/zsh-vi-mode-system-clipboard.plugin.zsh
  actual_files="$(
    find ${repoPluginsSource} -mindepth 1 -maxdepth 1 -type f -exec basename {} \; \
      | LC_ALL=C sort
  )"
  expected_files="$(printf '%s\n' \
    zsh-vi-mode-backward-kill-word.plugin.zsh \
    zsh-vi-mode-system-clipboard.plugin.zsh)"
  test "$actual_files" = "$expected_files"
  touch "$out"
''
