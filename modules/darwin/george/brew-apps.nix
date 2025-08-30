{
  homebrew = {
    casks = [
      "airfoil"
      "claude"
      "cursor"
      "datagrip"
      "docker-desktop"
      "figma"
      "framer"
      "ghostty@tip"
      "ghostty@tip"
      "gitbutler"
      "google-drive"
      "linear-linear"
      # brew 4.6.5 gained a `rename` function and logi-options+ uses it so
      # until nix-homebrew.inputs.brew-src is updated to that and made work
      # this will not be installed
      # "logi-options+"
      "loom"
      "macfuse"
      "nordvpn"
      "signal@beta"
      "visual-studio-code@insiders"
      "yaak@beta"
      "zed@preview"
      "zen@twilight"
      # "domzilla-caffeine"
    ];
    masApps = {
      "AdGuard for Safari" = 1440147259;
      "Amazon Kindle" = 302584613;
      "Apple Configurator" = 1037126344;
      "JSONPeep" = 1458969831;
      "Shazam: Identify Songs" = 897118787;
      # "Twitter" = 1482454543;
      "Xcode" = 497799835;
    };
  };
}
