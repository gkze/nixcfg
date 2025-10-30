{ lib, pkgs, ... }:
{
  services = {
    sketchybar = {
      enable = false;
      config = '''';
    };
    skhd = {
      enable = true;
      # skhdConfig = ''
      #   ctrl + alt - c : open -a /Applications/Cursor.app
      #   ctrl + alt - g : open -a /Applications/Ghostty.app
      #   ctrl + alt - t : open -a /Applications/Twilight.app
      #   ctrl + alt - z : open -a "/Applications/Zed Preview.app"
      # '';
    };
  };

  environment.systemPackages = with pkgs; [
    betterdisplay
    rectangle
  ];

  system.activationScripts.skhd-reload = {
    enable = true;
    text = "${lib.getExe pkgs.skhd} --reload";
  };
}
