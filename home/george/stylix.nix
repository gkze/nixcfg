{ pkgs, ... }:
{
  stylix = with pkgs; {
    enable = true;
    base16Scheme = "${base16-schemes}/share/themes/catppuccin-frappe.yaml";
    polarity = "dark";
    image = ./wallpaper.jpeg;
    targets = {
      bat.enable = false;
      nixvim.enable = false;
      # https://github.com/danth/stylix/issues/865
      gnome-text-editor.enable = pkgs.stdenv.isLinux;
      vscode.enable = false;
      gnome.enable = pkgs.stdenv.isLinux;
      gtk.enable = pkgs.stdenv.isLinux;
    };
    icons = {
      enable = !stdenv.isDarwin;
      package = papirus-icon-theme;
      dark = "Papirus-Dark";
    };
    cursor = {
      name = "catppuccin-frappe-blue-cursors";
      package = catppuccin-cursors.frappeBlue;
      size = 11;
    };
    fonts = {
      serif = {
        package = cantarell-fonts;
        name = "Cantarell";
      };
      sansSerif = {
        package = cantarell-fonts;
        name = "Cantarell";
      };
      monospace = {
        package = nerd-fonts.hack;
        name = "Hack Nerd Font Mono";
      };
      sizes = {
        applications = 11;
        desktop = 11;
        popups = 11;
        terminal = 11;
      };
    };
  };
}
