{ pkgs, ... }:
{
  services = {
    sketchybar = {
      enable = false;
      config = '''';
    };
    skhd = {
      enable = true;
      skhdConfig = ''
        cmd + shift - return : open -a /Applications/Arc.app
        cmd - return : open -a /Applications/Ghostty.app
      '';
    };
  };

  homebrew.casks = [
    "betterdisplay"
    "rectangle"
  ];

  system.activationScripts.skhd-reload = {
    enable = true;
    text = "${pkgs.skhd}/bin/skhd --reload";
  };
}
