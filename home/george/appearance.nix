{
  config,
  lib,
  pkgs,
  ...
}:
let
  state = "${config.xdg.stateHome}/nixcfg/appearance";
  toml = pkgs.formats.toml { };
  templates = lib.mapAttrs (
    mode: appearance:
    pkgs.linkFarm "appearance-${mode}" [
      {
        name = "alacritty.toml";
        path = "${config.catppuccin.sources.alacritty}/catppuccin-${appearance.variant}.toml";
      }
      {
        name = "helix.toml";
        path = toml.generate "helix-${mode}.toml" (
          config.programs.helix.settings
          // {
            theme = "catppuccin_${appearance.variant}";
          }
        );
      }
      {
        name = "superfile.toml";
        path = "${config.programs.superfile.package.src}/src/superfile_config/theme/catppuccin-${appearance.variant}.toml";
      }
      {
        name = "delta.gitconfig";
        path = pkgs.writeText "delta-${mode}.gitconfig" ''
          [delta]
            features = ${appearance.slug}
        '';
      }
      {
        name = "bat-config";
        path = pkgs.writeText "bat-${mode}.config" ''
          --theme='${appearance.displayName}'
        '';
      }
      {
        name = "mode";
        path = pkgs.writeText "appearance-mode" mode;
      }
    ]
  ) config.theme.appearances;
  sync = pkgs.writeShellApplication {
    name = "nixcfg-appearance-sync";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      # dark-mode-notify sets DARKMODE on launch and on each macOS appearance event.
      case "''${DARKMODE:-}" in
        0) source_dir=${templates.light} ;;
        1) source_dir=${templates.dark} ;;
        "")
          # Manual appearance overrides change the live appearance without
          # updating the persisted AppleInterfaceStyle preference, so detect
          # the live state instead of reading the defaults key.
          dark=$(
            /usr/bin/osascript -e 'tell application "System Events" to tell appearance preferences to get dark mode' 2>/dev/null ||
              echo false
          )
          if [ "$dark" = "true" ]; then
            source_dir=${templates.dark}
          else
            source_dir=${templates.light}
          fi
          ;;
        *) echo "Invalid DARKMODE: expected 0 or 1" >&2; exit 64 ;;
      esac
      state=${lib.escapeShellArg state}
      mkdir -p "$state"
      appearance_tmp=""
      trap 'if [ -n "$appearance_tmp" ]; then rm -f "$appearance_tmp"; fi' EXIT
      helix_changed=0
      for name in alacritty.toml helix.toml superfile.toml delta.gitconfig bat-config mode; do
        if cmp -s "$source_dir/$name" "$state/$name"; then continue; fi
        appearance_tmp=$(mktemp "$state/.$name.XXXXXX")
        cp "$source_dir/$name" "$appearance_tmp"
        chmod 600 "$appearance_tmp"
        mv "$appearance_tmp" "$state/$name"
        appearance_tmp=""
        if [ "$name" = helix.toml ]; then helix_changed=1; fi
      done
      # Helix 25.07 supports USR1 config reload. Superfile reloads on next launch.
      if [ "$helix_changed" = 1 ]; then
        /usr/bin/pkill -USR1 -u "$(id -u)" -x hx || [ "$?" = 1 ]
      fi
    '';
  };
in
{
  # Prefer app-native switching; bridge only the installed tools without it.
  stylix.targets.alacritty.enable = false;
  stylix.targets.helix.enable = false;
  catppuccin.alacritty.enable = false;
  catppuccin.helix.enable = false;
  programs.alacritty.settings = {
    general.import = [ "${state}/alacritty.toml" ];
    font = {
      normal.family = config.fonts.monospace.name;
      size = config.fonts.monospace.size;
    };
  };
  xdg.configFile."helix/config.toml".source = lib.mkForce (
    config.lib.file.mkOutOfStoreSymlink "${state}/helix.toml"
  );
  # bat re-reads its config on every invocation, so no reload signal is needed.
  # Its native auto:system mode tracks the persisted macOS preference, which
  # does not follow live appearance events under Auto scheduling.
  xdg.configFile."bat/config".source = lib.mkForce (
    config.lib.file.mkOutOfStoreSymlink "${state}/bat-config"
  );
  programs.superfile = {
    settings.theme = "nixcfg-system";
    themes.nixcfg-system = config.lib.file.mkOutOfStoreSymlink "${state}/superfile.toml";
  };
  nixcfg.git.deltaTheme = null;
  programs.git.includes = lib.mkAfter [ { path = "${state}/delta.gitconfig"; } ];
  home.sessionVariables.NIXCFG_APPEARANCE_FILE = "${state}/mode";
  home.packages = [ sync ];
  home.activation.initializeAppearance =
    lib.hm.dag.entryBetween [ "linkGeneration" ] [ "writeBoundary" ]
      ''
        run ${lib.getExe sync}
      '';
  launchd.agents.nixcfg-appearance = {
    enable = true;
    config = {
      Label = "dev.george.nixcfg-appearance";
      ProgramArguments = [
        (lib.getExe pkgs.dark-mode-notify)
        (lib.getExe sync)
      ];
      RunAtLoad = true;
      KeepAlive = true;
      StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/nixcfg-appearance.log";
    };
  };
  # dark-mode-notify only fires on events that update the persisted macOS
  # preference. Manual appearance overrides (for example from Control Center
  # while Auto scheduling is active) change the live appearance without either
  # writing the preference or raising that event, so poll the live state.
  launchd.agents.nixcfg-appearance-poll = {
    enable = true;
    config = {
      Label = "dev.george.nixcfg-appearance-poll";
      ProgramArguments = [ (lib.getExe sync) ];
      RunAtLoad = true;
      StartInterval = 30;
      StandardErrorPath = "${config.home.homeDirectory}/Library/Logs/nixcfg-appearance-poll.log";
    };
  };
}
