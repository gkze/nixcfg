{ config, pkgs, ... }:
let
  inherit (config) theme fonts;
in
{
  stylix = with pkgs; {
    enable = true;
    base16Scheme = "${base16-schemes}/share/themes/${theme.slug}.yaml";
    inherit (theme) polarity;
    image = ./wallpaper.jpeg;
    targets = {
      # Disabled: using catppuccin-bat theme directly in bat config
      bat.enable = false;
      # Disabled: nixvim has its own colorscheme config (catppuccin)
      nixvim.enable = false;
      # https://github.com/danth/stylix/issues/865
      gnome-text-editor.enable = pkgs.stdenv.isLinux;
      # Disabled: VS Code manages themes via extensions
      vscode.enable = false;
      # Disabled: using built-in catppuccin-frappe theme (better diff colors, panel hierarchy)
      opencode.enable = false;
      # Disabled: managing zed config directly with sops secret injection
      zed.enable = false;
      gnome.enable = pkgs.stdenv.isLinux;
      gtk.enable = pkgs.stdenv.isLinux;
    };
    icons = {
      enable = !stdenv.isDarwin;
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
        inherit (fonts.serif) package name;
      };
      sansSerif = {
        inherit (fonts.sansSerif) package name;
      };
      monospace = {
        inherit (fonts.monospace) package name;
      };
      sizes = {
        applications = fonts.monospace.size;
        desktop = fonts.monospace.size;
        popups = fonts.monospace.size;
        terminal = fonts.monospace.size;
      };
    };
  };
}
