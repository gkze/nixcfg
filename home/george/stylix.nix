{ pkgs, ... }:
{
  stylix = with pkgs; {
    enable = true;
    base16Scheme = "${base16-schemes}/share/themes/catppuccin-frappe.yaml";
    polarity = "dark";
    image = ./wallpaper.jpeg;
    targets.nixvim.enable = false;
    iconTheme = {
      enable = !stdenv.isDarwin;
      package = papirus-icon-theme;
      dark = "Papirus-Dark";
    };
    cursor = {
      package = catppuccin-cursors.frappeBlue;
      name = "catppuccin-frappe-blue-cursors";
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
