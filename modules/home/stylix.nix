{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (config) fonts theme;
  cfg = config.nixcfg.stylix;
in
{
  options.nixcfg.stylix = {
    enable = lib.mkEnableOption "Stylix theming integration" // {
      default = true;
    };

    base16Scheme = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Optional explicit base16 scheme path. Defaults to theme.slug mapping.";
    };

    wallpaper = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Optional wallpaper path used for Stylix image-based theming.";
    };

    enableLinuxDesktopTargets = lib.mkOption {
      type = lib.types.bool;
      default = pkgs.stdenv.isLinux;
      description = "Enable Linux desktop-related Stylix targets (gnome, gtk, gnome-text-editor).";
    };

    enableGhosttyTarget = lib.mkOption {
      type = lib.types.bool;
      default = !pkgs.stdenv.isDarwin;
      description = "Enable Stylix's Ghostty target.";
    };

    enableIcons = lib.mkOption {
      type = lib.types.bool;
      default = !pkgs.stdenv.isDarwin;
      description = "Enable Stylix icon theme configuration.";
    };
  };

  config = lib.mkIf cfg.enable {
    stylix =
      (with pkgs; {
        enable = true;
        overlays.enable = false;
        base16Scheme =
          if cfg.base16Scheme != null then
            cfg.base16Scheme
          else
            "${base16-schemes}/share/themes/${theme.slug}.yaml";
        inherit (theme) polarity;
        targets = {
          bat.enable = false;
          nixvim.enable = false;
          neovide.enable = false;
          gnome-text-editor.enable = cfg.enableLinuxDesktopTargets;
          vscode.enable = false;
          opencode.enable = false;
          zed.enable = false;
          ghostty.enable = cfg.enableGhosttyTarget;
          gnome.enable = cfg.enableLinuxDesktopTargets;
          gtk.enable = cfg.enableLinuxDesktopTargets;
        };
        icons = {
          enable = cfg.enableIcons;
          package = papirus-icon-theme;
          dark = "Papirus-Dark";
        };
        cursor = {
          name = "${theme.slug}-${theme.accentColor}-cursors";
          package =
            catppuccin-cursors."${theme.variant}${
              let
                c = theme.accentColor;
              in
              (lib.toUpper (builtins.substring 0 1 c)) + (builtins.substring 1 (builtins.stringLength c - 1) c)
            }";
          inherit (fonts.monospace) size;
        };
        fonts = {
          serif = {
            inherit (fonts.serif)
              name
              package
              ;
          };
          sansSerif = {
            inherit (fonts.sansSerif)
              name
              package
              ;
          };
          monospace = {
            inherit (fonts.monospace)
              name
              package
              ;
          };
          sizes = {
            applications = fonts.monospace.size;
            desktop = fonts.monospace.size;
            popups = fonts.monospace.size;
            terminal = fonts.monospace.size;
          };
        };
      })
      // lib.optionalAttrs (cfg.wallpaper != null) {
        image = cfg.wallpaper;
      };
  };
}
