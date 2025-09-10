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
        alt + shift - c : open -a /Applications/Cursor.app
        alt + shift - return : open -a /Applications/Arc.app
        alt - return : open -a /Applications/Ghostty.app
      '';
    };
  };

  homebrew.casks = [
    "betterdisplay"
    "rectangle"
  ];

  system.activationScripts.skhd-reload = {
    enable = true;
    text = "${lib.getExe pkgs.skhd} --reload";
  };
}
