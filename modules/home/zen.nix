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

  zenSync = pkgs.writeShellApplication {
    name = "zen-sync";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.findutils
    ];
    text = ''
      set -euo pipefail

      zen_folders_cmd=(${lib.escapeShellArg cfg.foldersCommand})
      profile_selector=${lib.escapeShellArg (cfg.profile or "")}
      profile_args=()
      if [ -n "$profile_selector" ]; then
        profile_args+=(--profile "$profile_selector")
      fi

      managed_config_dir=${lib.escapeShellArg managedConfigDir}
      managed_chrome_dir="$managed_config_dir/chrome"
      managed_user_js="$managed_config_dir/user.js"
      managed_folders="$managed_config_dir/folders.yaml"

      link_managed_file() {
        local src="$1"
        local dest="$2"

        if [ -e "$src" ]; then
          ${lib.getExe' pkgs.coreutils "mkdir"} -p "$(dirname "$dest")"
          ${lib.getExe' pkgs.coreutils "ln"} -sfn "$src" "$dest"
          return 0
        fi

        if [ -L "$dest" ]; then
          target="$(readlink "$dest" || true)"
          if [ "$target" = "$src" ]; then
            ${lib.getExe' pkgs.coreutils "rm"} -f "$dest"
          fi
        fi
      }

      cleanup_managed_chrome_symlinks() {
        while IFS= read -r -d $'\0' dest; do
          target="$(readlink "$dest" || true)"
          case "$target" in
            "$managed_chrome_dir"/*)
              rel="''${target#"$managed_chrome_dir"/}"
              if [ ! -e "$managed_chrome_dir/$rel" ]; then
                ${lib.getExe' pkgs.coreutils "rm"} -f "$dest"
              fi
              ;;
          esac
        done < <(${lib.getExe' pkgs.findutils "find"} "$profile_chrome_dir" -type l -print0)
      }

      if ! profile_dir="$("''${zen_folders_cmd[@]}" "''${profile_args[@]}" profile-path 2>/dev/null)"; then
        echo "zen-sync: no Zen profile detected yet; launch Zen/Twilight once, then rerun." >&2
        exit 0
      fi

      profile_chrome_dir="$profile_dir/chrome"
      ${lib.getExe' pkgs.coreutils "mkdir"} -p "$profile_chrome_dir"

      if [ -d "$managed_chrome_dir" ]; then
        while IFS= read -r -d $'\0' src; do
          rel="''${src#"$managed_chrome_dir"/}"
          dest="$profile_chrome_dir/$rel"
          ${lib.getExe' pkgs.coreutils "mkdir"} -p "$(dirname "$dest")"
          ${lib.getExe' pkgs.coreutils "ln"} -sfn "$src" "$dest"
        done < <(${lib.getExe' pkgs.findutils "find"} -L "$managed_chrome_dir" -type f -print0)
      fi
      cleanup_managed_chrome_symlinks

      link_managed_file "$managed_user_js" "$profile_dir/user.js"

      if "''${zen_folders_cmd[@]}" "''${profile_args[@]}" is-running >/dev/null 2>&1; then
        echo "zen-sync: Zen is running; synced chrome files and prefs, skipped folder reconciliation." >&2
        exit 0
      fi

      ${lib.optionalString cfg.applyFoldersOnActivation ''
        if [ -e "$managed_folders" ]; then
          "''${zen_folders_cmd[@]}" "''${profile_args[@]}" apply -y -c "$managed_folders"
        fi
      ''}
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

    foldersCommand = mkOption {
      type = types.str;
      default = "${config.home.homeDirectory}/.local/bin/zen-folders";
      description = ''
        Command used to reconcile declarative Zen folders. Defaults to the
        repo-managed zen-folders script in ~/.local/bin.
      '';
    };

    syncOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Run zen-sync during Home Manager activation.";
    };

    applyFoldersOnActivation = mkOption {
      type = types.bool;
      default = true;
      description = "Apply folders.yaml during zen-sync when Zen is closed.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.syncOnActivation -> cfg.foldersCommand != "";
        message = "nixcfg.zen.foldersCommand must be non-empty when syncOnActivation is enabled.";
      }
    ];

    home.packages = [ zenSync ];

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
        run --silence ${lib.getExe zenSync}
      '';
    };
  };
}
