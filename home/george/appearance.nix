{
  config,
  inputs,
  lib,
  pkgs,
  ...
}:
let
  state = "${config.xdg.stateHome}/nixcfg/appearance";
  toml = pkgs.formats.toml { };
  # Read Starship palettes from an evaluator-visible input, not catppuccin/nix's
  # port derivation: importing a derivation output would force a native build
  # at evaluation time and break cross-platform evaluation of Darwin roots.
  starshipThemes =
    let
      inputSource = inputs.catppuccin-starship-src;
      moduleSource = (lib.importJSON "${inputs.catppuccin}/pkgs/sources.json").starship;
    in
    assert lib.assertMsg (
      inputSource.rev == moduleSource.rev && inputSource.narHash == moduleSource.hash
    ) "catppuccin-starship-src is out of sync with catppuccin/nix's Starship source.";
    "${inputSource}/themes";
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
        name = "starship.toml";
        path =
          let
            paletteName = "catppuccin_${appearance.variant}";
            palette =
              (lib.importTOML "${starshipThemes}/${appearance.variant}.toml").palettes.${paletteName};
            # Preserve Starship's default styles, which use ANSI color names.
            colors = palette // {
              purple = palette.mauve;
              magenta = palette.mauve;
              cyan = palette.teal;
              white = palette.text;
              black = palette.crust;
              orange = palette.peach;
              brown = palette.flamingo;
            };
          in
          toml.generate "starship-${mode}.toml" (
            lib.recursiveUpdate config.programs.starship.settings {
              palette = paletteName;
              palettes.${paletteName} =
                colors
                // lib.listToAttrs (
                  map (name: lib.nameValuePair "bright-${name}" colors.${name}) [
                    "black"
                    "red"
                    "green"
                    "yellow"
                    "blue"
                    "purple"
                    "magenta"
                    "cyan"
                    "white"
                  ]
                );
            }
          );
      }
      {
        name = "delta.gitconfig";
        path = pkgs.writeText "delta-${mode}.gitconfig" ''
          [delta]
            features = ${appearance.slug}
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
          if [ "$(/usr/bin/defaults read -g AppleInterfaceStyle 2>/dev/null || true)" = Dark ]; then
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
      for name in alacritty.toml helix.toml superfile.toml starship.toml delta.gitconfig mode; do
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
  stylix.targets.starship.enable = false;
  catppuccin.alacritty.enable = false;
  catppuccin.helix.enable = false;
  catppuccin.starship.enable = false;
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
  home.file.${config.programs.starship.configPath}.source = lib.mkForce (
    config.lib.file.mkOutOfStoreSymlink "${state}/starship.toml"
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
}
