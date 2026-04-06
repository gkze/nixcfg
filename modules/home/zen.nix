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

            usage() {
              cat <<'EOF'
      Usage: zen-sync [--chrome-source PATH]

      Options:
        --chrome-source PATH  Sync chrome assets directly from PATH instead of
                              ~/.config/zen/chrome.
        --help                Show this help text.
      EOF
            }

            chrome_source_override="''${ZEN_CHROME_SOURCE:-}"
            while [ "$#" -gt 0 ]; do
              case "$1" in
                --chrome-source)
                  if [ "$#" -lt 2 ]; then
                    echo "zen-sync: --chrome-source requires a path argument." >&2
                    exit 2
                  fi
                  chrome_source_override="$2"
                  shift 2
                  ;;
                --help)
                  usage
                  exit 0
                  ;;
                *)
                  echo "zen-sync: unknown argument: $1" >&2
                  usage >&2
                  exit 2
                  ;;
              esac
            done

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

            resolve_chrome_source_dir() {
              local candidate="$1"

              if [ -z "$candidate" ]; then
                return 1
              fi
              if [ ! -d "$candidate" ]; then
                echo "zen-sync: chrome source directory not found: $candidate" >&2
                exit 1
              fi

              ${lib.getExe' pkgs.coreutils "realpath"} "$candidate"
            }

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
              local manifest="$1"

              if [ ! -f "$manifest" ]; then
                return 0
              fi

              while IFS= read -r rel; do
                if [ -z "$rel" ]; then
                  continue
                fi

                dest="$profile_chrome_dir/$rel"
                if [ -L "$dest" ]; then
                  ${lib.getExe' pkgs.coreutils "rm"} -f "$dest"
                fi
              done < "$manifest"
            }

            prune_empty_chrome_dirs() {
              ${lib.getExe' pkgs.findutils "find"} "$profile_chrome_dir" -depth -type d -empty -delete
            }

            sync_chrome_tree() {
              local source_dir="$1"
              local manifest="$2"

              if [ -z "$source_dir" ]; then
                return 0
              fi

              while IFS= read -r -d $'\0' src; do
                rel="''${src#"$source_dir"/}"
                dest="$profile_chrome_dir/$rel"
                ${lib.getExe' pkgs.coreutils "mkdir"} -p "$(dirname "$dest")"
                ${lib.getExe' pkgs.coreutils "ln"} -sfn "$src" "$dest"
                printf '%s\n' "$rel" >> "$manifest"
              done < <(${lib.getExe' pkgs.findutils "find"} -L "$source_dir" -type f -print0)
            }

            if ! profile_dir="$("''${zen_folders_cmd[@]}" "''${profile_args[@]}" profile-path 2>/dev/null)"; then
              echo "zen-sync: no Zen profile detected yet; launch Zen/Twilight once, then rerun." >&2
              exit 0
            fi

            profile_chrome_dir="$profile_dir/chrome"
            ${lib.getExe' pkgs.coreutils "mkdir"} -p "$profile_chrome_dir"

            managed_chrome_manifest="$profile_dir/.nixcfg-zen-managed-chrome"
            chrome_source_dir=""
            if [ -n "$chrome_source_override" ]; then
              chrome_source_dir="$(resolve_chrome_source_dir "$chrome_source_override")"
            elif [ -d "$managed_chrome_dir" ]; then
              chrome_source_dir="$(resolve_chrome_source_dir "$managed_chrome_dir")"
            fi

            cleanup_managed_chrome_symlinks "$managed_chrome_manifest"
            prune_empty_chrome_dirs

            if [ -n "$chrome_source_dir" ]; then
              manifest_tmp="$(${lib.getExe' pkgs.coreutils "mktemp"} "$profile_dir/.nixcfg-zen-managed-chrome.XXXXXX")"
              sync_chrome_tree "$chrome_source_dir" "$manifest_tmp"
              ${lib.getExe' pkgs.coreutils "mv"} "$manifest_tmp" "$managed_chrome_manifest"
            else
              ${lib.getExe' pkgs.coreutils "rm"} -f "$managed_chrome_manifest"
            fi

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

        For local theme iteration without a Nix rebuild, run
        `zen-sync --chrome-source /path/to/chrome` (or set
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
