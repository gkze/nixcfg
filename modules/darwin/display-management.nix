{ lib, pkgs, ... }:
{
  services = {
    sketchybar = {
      enable = false;
      config = '''';
    };
    skhd = {
      enable = true;
      skhdConfig = ''
        alt + shift - c : open /Applications/Cursor.app
        alt + shift - g : open /Applications/Ghostty.app
        alt + shift - t : open /Applications/Twilight.app
        alt + shift - z : open "/Applications/Zed Preview.app"
      '';
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
