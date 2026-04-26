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
      click
      deepdiff
      lz4
      pydantic
      pyyaml
      typer
    ]
  );
  mkZenWrapper =
    name: script:
    pkgs.writeShellApplication {
      inherit name;
      text = ''
        exec ${lib.getExe zenPython} ${lib.escapeShellArg script} "$@"
      '';
    };
  zenTool = mkZenWrapper "zentool" ../../home/george/bin/zentool;
in
{
  options.nixcfg.zen = {
    enable = mkEnableOption "Zen/Twilight profile sync and declarative customizations";

    profile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "Default (twilight)";
      description = ''
        Profile selector passed through to zentool. Accepts a profile
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
        `zentool apply --assets --chrome-source /path/to/chrome` (or set
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
        Declarative Zen session config published to ~/.config/zen/folders.yaml
        and applied with zentool when Zen is closed. The schema is
        essentials/workspaces/items/tabs, with exact syncing against the
        managed subset of zen-sessions.jsonlz4.
      '';
    };

    toolCommand = mkOption {
      type = types.str;
      default = lib.getExe zenTool;
      description = ''
        Command used to inspect and reconcile Zen state and assets. Defaults to
        the packaged repo-managed zentool wrapper.
      '';
    };

    syncOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Run zentool apply during Home Manager activation.";
    };

    applyStateOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply folders.yaml to the Zen session during activation.";
    };

    applyAssetsOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply managed Zen assets during activation.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.syncOnActivation -> cfg.toolCommand != "";
        message = "nixcfg.zen.toolCommand must be non-empty when syncOnActivation is enabled.";
      }
    ];

    home.packages = [
      zenTool
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
        sync_cmd=(${lib.escapeShellArg cfg.toolCommand})
        sync_args=(apply --yes)
        profile_args=()
        state_args=()
        asset_args=()
        state_config=${lib.escapeShellArg (managedConfigDir + "/folders.yaml")}

        ${lib.optionalString (cfg.profile != null) ''
          profile_args+=(--profile ${lib.escapeShellArg cfg.profile})
        ''}

        ${lib.optionalString cfg.applyStateOnActivation ''
          if [ -e "$state_config" ]; then
            state_args+=(--state)
            state_args+=(--config "$state_config")
          fi
        ''}

        ${lib.optionalString cfg.applyAssetsOnActivation ''
          asset_args+=(--assets)
          asset_args+=(--asset-dir ${lib.escapeShellArg managedConfigDir})
        ''}

        if [ "''${#state_args[@]}" -gt 0 ]; then
          runtime_check_cmd=("''${sync_cmd[@]}" profile)
          if [ "''${#profile_args[@]}" -gt 0 ]; then
            runtime_check_cmd+=("''${profile_args[@]}")
          fi
          runtime_check_cmd+=(is-running)

          if "''${runtime_check_cmd[@]}" >/dev/null 2>&1; then
            echo "warning: skipping Zen state sync during activation because Zen is running" >&2
            state_args=()
          fi
        fi

        if [ "''${#state_args[@]}" -gt 0 ]; then
          sync_args+=("''${state_args[@]}")
        fi

        if [ "''${#asset_args[@]}" -gt 0 ]; then
          sync_args+=("''${asset_args[@]}")
        fi

        if [ "''${#profile_args[@]}" -gt 0 ]; then
          sync_args+=("''${profile_args[@]}")
        fi

        if [ "''${#state_args[@]}" -gt 0 ] || [ "''${#asset_args[@]}" -gt 0 ]; then
          run --silence "''${sync_cmd[@]}" "''${sync_args[@]}"
        fi
      '';
    };
  };
}
