{ lib, pkgs, profiles, ... }:
{
  # Homebrew & mas
  homebrew = {
    # Only manage Homebrew when underlying system is macOS
    enable = pkgs.stdenv.hostPlatform.isDarwin;

    # Stay updated & clean up things that are not defined below
    onActivation = { autoUpdate = true; cleanup = "zap"; upgrade = true; };

    # Homebrew Taps
    taps = [ "homebrew/cask-fonts" "homebrew/cask-versions" "gkze/gkze" ];

    # Homebrew Formulae
    brews = [ "sheldon" "stars" ];

    # Homebrew Casks
    casks = [
      "aerial"
      "alacritty"
      "appcleaner"
      "arctype"
      "beekeeper-studio"
      "brave-browser-beta"
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
      "maccy"
      "messenger"
      "monitorcontrol"
      "netnewswire-beta"
      "openlens"
      "osquery"
      "rapidapi"
      "rectangle"
      "signal"
      "skype"
      "slack"
      "sloth"
      "spotify"
      "tableplus"
      "telegram"
      "utm"
      "vagrant"
      "vimr"
      "visual-studio-code-insiders"
      "vlc"
      "whatsapp"
      "xcodes"
      "zoom"
    ] ++
    # Plaid-restricted software
    (lib.optionals (!builtins.elem "plaid" profiles) [ "nordvpn" "webtorrent" ]);

    # mas manages macOS App Store Apps via a CLI
    masApps = {
      "AdGuard for Safari" = 1440147259;
      "Apple Configurator" = 1037126344;
      "JSONPeep" = 1458969831;
      "Table Tool" = 1122008420;
      "Twitter" = 1482454543;
      "Xcode" = 497799835;
    };
  };
}
