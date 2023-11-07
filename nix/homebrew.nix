{ pkgs, ... }: {
  # Homebrew & mas
  homebrew = {
    # Only manage Homebrew when underlying system is macOS
    enable = pkgs.stdenv.isDarwin;

    # Stay updated & clean up things that are not defined below
    onActivation = { autoUpdate = true; cleanup = "zap"; upgrade = true; };

    # Homebrew Taps
    taps = [ "gkze/gkze" "homebrew/cask-fonts" "homebrew/cask-versions" ];

    # Homebrew Formulae
    brews = [ "bash-language-server" "sheldon" "stars" ];

    # Homebrew Casks
    casks = [
      "aerial"
      "alacritty"
      "appcleaner"
      "beekeeper-studio"
      "beeper"
      "brave-browser-beta"
      "codeedit"
      "cyberduck"
      "dash"
      "discord"
      "docker"
      "element"
      "figma"
      "firefox"
      "font-hack-nerd-font"
      "font-source-code-pro-for-powerline"
      "gimp"
      "github"
      "google-chrome"
      "google-cloud-sdk"
      "google-drive"
      "hot"
      "httpie"
      "low-profile"
      "maccy"
      "messenger"
      "monitorcontrol"
      "musicbrainz-picard"
      "netnewswire-beta"
      "nordvpn"
      "openlens"
      "rapidapi"
      "rectangle"
      "rekordbox"
      "signal"
      "skype"
      "slack"
      "sloth"
      "soulseek"
      "spotify"
      "tableplus"
      "telegram"
      "utm"
      "vagrant"
      "vimr"
      "visual-studio-code-insiders"
      "vlc"
      "webtorrent"
      "whatsapp"
      "xcodes"
      "zoom"
    ];

    # mas manages macOS App Store Apps via a CLI
    masApps = {
      "AdGuard for Safari" = 1440147259;
      "Apple Configurator" = 1037126344;
      "JSONPeep" = 1458969831;
      "Shazam: Identify Songs" = 897118787;
      "Table Tool" = 1122008420;
      "Twitter" = 1482454543;
      "Xcode" = 497799835;
    };
  };
}
