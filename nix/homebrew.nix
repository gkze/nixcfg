{ pkgs, lib, ... }: {
  # Homebrew & mas
  homebrew = {
    # Only manage Homebrew when underlying system is macOS
    # (Maybe try on other OSes in the future)
    enable = pkgs.stdenv.isDarwin;

    # Stay updated & clean up things that are not defined below
    onActivation = { autoUpdate = true; cleanup = "zap"; upgrade = true; };

    # Homebrew Taps
    taps = [ "gkze/gkze" "homebrew/cask-fonts" "homebrew/cask-versions" ];

    # Homebrew Formulae
    brews = [ ];

    # Homebrew Casks. They've been split out to what I believe to be
    # cross-platform vs. macOS-only casks (in case we'd like to use Homebrew
    # outside of macOS in the future for whatever reason)
    # TODO: refactor into os / device / profile based installation
    casks = [
      # "discord"
      # "docker"
      # "figma"
      # "firefox"
      # "gimp"
      # "github"
      # "google-chrome"
      # "google-drive"
      # "httpie"
      # "low-profile"
      # "messenger"
      # "musicbrainz-picard"
      # "nordvpn"
      # "openlens"
      # "signal"
      # "skype"
      # "slack"
      # "soulseek"
      # "spotify"
      # "telegram"
      # "vagrant"
      # "visual-studio-code-insiders"
      # "vlc"
      # "webtorrent"
      # "whatsapp"
      # "zoom"
    ] ++ (lib.optionals pkgs.stdenv.isDarwin [
      "brave-browser-beta"
      "beekeeper-studio"
      "aerial"
      "appcleaner"
      "codeedit"
      "cog"
      "cyberduck"
      "dash"
      "hot"
      "maccy"
      "monitorcontrol"
      "netnewswire-beta"
      "rapidapi"
      "rectangle"
      "rekordbox"
      "sloth"
      "tableplus"
      "utm"
      "vimr"
      "xcodes"
    ]);
  };
}
