{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    ;

  cfg = config.nixcfg.zen;
  managedConfigDir = "${config.xdg.configHome}/zen";
  zenPython = pkgs.python3.withPackages (
    ps: with ps; [
      lz4
      pyyaml
    ]
  );
  zenProfileSync = pkgs.writeShellApplication {
    name = "zen-profile-sync";
    text = ''
      exec ${lib.getExe zenPython} ${lib.escapeShellArg ../../home/george/bin/zen-profile-sync} "$@"
    '';
  };
  zenFolders = pkgs.writeShellApplication {
    name = "zen-folders";
    text = ''
      exec ${lib.getExe zenPython} ${lib.escapeShellArg ../../home/george/bin/zen-folders} "$@"
    '';
  };
in
{
  options.nixcfg.zen = {
    enable = mkEnableOption "Zen/Twilight profile sync and declarative customizations";

    profile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "Default (twilight)";
      description = ''
        Profile selector passed through to zen-folders. Accepts a profile
        directory name, a direct path, or a human profile name from
        profiles.ini. Null uses auto-detection from Zen's profiles.ini.
      '';
    };

    chromeSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./chrome;
      description = ''
        Directory of managed Zen chrome assets. It is published to
        ~/.config/zen/chrome and each file is symlinked into the live profile's
        chrome/ directory.

        For local theme iteration without a Nix rebuild, run
        `zen-profile-sync --chrome-source /path/to/chrome` (or set
        `ZEN_CHROME_SOURCE=/path/to/chrome`) to temporarily sync from a direct
        filesystem path instead.
      '';
    };

    userJsSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./user.js;
      description = ''
        Source file for ~/.config/zen/user.js, symlinked into the live profile
        root.
      '';
    };

    foldersSource = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = ./folders.yaml;
      description = ''
        Declarative folder config published to ~/.config/zen/folders.yaml and
        applied with zen-folders when Zen is closed. Supports an optional
        __workspace__ block for workspace metadata like icon,
        hasCollapsedPinnedTabs, and theme. containerTabId is used when creating
        a missing workspace.
      '';
    };

    profileSyncCommand = mkOption {
      type = types.str;
      default = lib.getExe zenProfileSync;
      description = ''
        Command used to sync managed chrome assets, user.js, and optional
        folders.yaml into the live Zen profile. Defaults to the packaged
        repo-managed zen-profile-sync wrapper.
      '';
    };

    foldersCommand = mkOption {
      type = types.str;
      default = lib.getExe zenFolders;
      description = ''
        Command used to reconcile declarative Zen folders. Defaults to the
        packaged repo-managed zen-folders wrapper.
      '';
    };

    syncOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Run zen-profile-sync during Home Manager activation.";
    };

    applyFoldersOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply folders.yaml during zen-profile-sync when Zen is closed.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.syncOnActivation -> cfg.profileSyncCommand != "";
        message = "nixcfg.zen.profileSyncCommand must be non-empty when syncOnActivation is enabled.";
      }
      {
        assertion = cfg.syncOnActivation -> cfg.foldersCommand != "";
        message = "nixcfg.zen.foldersCommand must be non-empty when syncOnActivation is enabled.";
      }
    ];

    home.packages = [
      zenProfileSync
      zenFolders
    ];

    xdg.configFile = mkMerge [
      (mkIf (cfg.chromeSource != null) {
        "zen/chrome" = {
          source = cfg.chromeSource;
          recursive = true;
        };
      })
      (mkIf (cfg.userJsSource != null) {
        "zen/user.js".source = cfg.userJsSource;
      })
      (mkIf (cfg.foldersSource != null) {
        "zen/folders.yaml".source = cfg.foldersSource;
      })
    ];

    home.activation = mkIf cfg.syncOnActivation {
      nixcfgZenSync = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
        sync_cmd=(${lib.escapeShellArg cfg.profileSyncCommand})
        sync_args=(
          --config-dir ${lib.escapeShellArg managedConfigDir}
          --folders-command ${lib.escapeShellArg cfg.foldersCommand}
          ${if cfg.applyFoldersOnActivation then "--apply-folders" else "--no-apply-folders"}
        )
        ${lib.optionalString (cfg.profile != null) ''
          sync_args+=(--profile ${lib.escapeShellArg cfg.profile})
        ''}
        run --silence "''${sync_cmd[@]}" "''${sync_args[@]}"
      '';
    };
  };
}
