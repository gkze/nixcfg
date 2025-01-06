{ pkgs, src, ... }:
{
  environment.systemPackages = with pkgs; [
    (stdenvNoCC.mkDerivation {
      name = "yabai-center";
      src = "${src}/misc/yabai-center";
      dontUnpack = true;
      installPhase = ''
        mkdir -p $out/bin
        cp $src $out/bin/yabai-center
      '';
    })
  ];

  services = {
    sketchybar = {
      enable = false;
      config = ''

      '';
    };
    skhd = {
      enable = true;
      skhdConfig = ''
        cmd + shift - return : open -a /Applications/Arc.app
        cmd - return : open -a /Applications/Ghostty.app
        shift + cmd - c : yabai-center
        shift + cmd - left : yabai -m window --grid 1:2:0:0:1:1
        shift + cmd - right : yabai -m window --grid 1:2:1:0:1:1
        shift + cmd - up : yabai -m window --toggle windowed-fullscreen
      '';
    };
    yabai.enable = true;
  };

  system.activationScripts.skhd-reload = {
    enable = true;
    text = "${pkgs.skhd}/bin/skhd --reload";
  };
}
